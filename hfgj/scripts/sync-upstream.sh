#!/usr/bin/env bash
# Cloud-only routine update. Never overwrite the patch branch on conflict.
set -euo pipefail
git diff --quiet && git diff --cached --quiet
test -z "$(git ls-files --others --exclude-standard)"
git fetch upstream main
git fetch origin main hfgj
git merge-base --is-ancestor origin/main upstream/main || {
  echo 'Mirror main diverged; refusing to overwrite it' >&2; exit 1;
}
git checkout -B hfgj-sync-candidate origin/hfgj
git config user.name 'HFGJ upstream sync'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
if ! git merge --no-edit upstream/main; then
  git merge --abort
  echo 'Conflict: existing mirror, patch branch and published feed retained. Resolve on GitHub before retrying.' >&2
  exit 1
fi
python3 -m unittest discover -s hfgj/tests -v
for script in feed.sh install.sh migrate.sh migrate-job.sh rollback-package.sh; do sh -n "$script" || exit; done
bash -n hfgj/scripts/setup-opkg.sh
# Push the mirror and patch together; a racing manual push fails instead of being overwritten.
git push --atomic origin upstream/main:refs/heads/main HEAD:refs/heads/hfgj
