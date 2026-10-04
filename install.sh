#!/bin/sh
# Run after downloading the bootstrap files, not through a curl-to-shell pipe.
set -eu
HFGJ_FEED_URL=${HFGJ_FEED_URL:-https://hfgj.github.io/OpenWrt-nikki}
hfgj_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -f "$hfgj_script_dir/feed.sh" ] && [ -f "$hfgj_script_dir/migrate.sh" ] && [ -f "$hfgj_script_dir/rollback-package.sh" ] || {
    echo 'Download feed.sh, install.sh, migrate.sh and rollback-package.sh into the same directory first' >&2; exit 1;
}
case "${1:-}" in
    --apply) ;;
    *) echo 'Usage: sh install.sh --apply (adds feeds and installs/migrates the core and official Nikki)'; exit 1 ;;
esac
sh "$hfgj_script_dir/feed.sh"
sh "$hfgj_script_dir/migrate.sh" --apply
# Install only after the HFGJ core provides the virtual dependency.
opkg status mihomo-hfgj | grep -q '^Status: .* installed$'
opkg install nikki luci-app-nikki
hfgj_languages=$(opkg list-installed 'luci-i18n-base-*' | awk '{sub(/^luci-i18n-base-/, "", $1); print $1}')
for hfgj_language in $hfgj_languages; do
    case "$hfgj_language" in *[!a-zA-Z0-9_-]*) echo 'Invalid language identifier' >&2; exit 1 ;; esac
    opkg install "luci-i18n-nikki-$hfgj_language"
done
echo 'Installed HFGJ core and official Nikki packages. Check Nikki service status and provider connectivity.'
