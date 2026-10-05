#!/usr/bin/env bash
# Read-only check immediately before Pages deployment; never move remote refs.
set -euo pipefail
expected=${1:?Expected locked packaging SHA is required}
[[ "$expected" =~ ^[0-9a-f]{40}$ ]] || {
  echo 'Invalid locked packaging SHA' >&2; exit 1;
}
[[ "$(git rev-parse HEAD)" = "$expected" ]] || {
  echo 'Checkout differs from locked packaging source' >&2; exit 1;
}
remote_ref=$(git ls-remote --exit-code origin refs/heads/hfgj)
[[ "$remote_ref" = "$expected"$'\trefs/heads/hfgj' ]] || {
  echo 'Packaging source is outdated; publication stopped. Run a fresh feed build.' >&2
  exit 1
}
echo 'Locked packaging source still matches remote hfgj'
