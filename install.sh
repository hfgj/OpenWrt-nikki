#!/bin/sh
# Run after downloading the bootstrap files, not through a curl-to-shell pipe.
set -eu
HFGJ_FEED_URL=${HFGJ_FEED_URL:-https://hfgj.github.io/OpenWrt-nikki}
hfgj_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "${1:-}" in
    --apply|--apply-fresh|--prepare-tools|--check-bootstrap) [ $# = 1 ] || exit 1 ;;
    *) echo 'Usage: sh install.sh --prepare-tools | --apply | --apply-fresh | --check-bootstrap'; exit 1 ;;
esac
cd "$hfgj_script_dir"
for hfgj_name in setup.sh feed.sh install.sh migrate.sh migrate-job.sh rollback-package.sh bootstrap.sha256; do
    [ -f "$hfgj_name" ] || { echo "Missing bootstrap file: $hfgj_name; download all six scripts and bootstrap.sha256 from the same reviewed feed" >&2; exit 1; }
done
# Require coverage of exactly these files, not an empty or partial manifest.
awk '
    BEGIN {split("setup.sh feed.sh install.sh migrate.sh migrate-job.sh rollback-package.sh", names, " "); for(i in names) expected[names[i]]=1}
    NF!=2 || length($1)!=64 || $1~/[^0-9a-f]/ || !($2 in expected) || seen[$2]++ {bad=1}
    END {exit (bad || NR!=6)}
' bootstrap.sha256 || { echo 'Invalid or incomplete bootstrap manifest; nothing changed' >&2; exit 1; }
sha256sum -c bootstrap.sha256
[ "$1" != --check-bootstrap ] || exit 0
[ "$(id -u)" = 0 ] || { echo 'Run as root' >&2; exit 1; }
command -v opkg >/dev/null || { echo 'Only opkg is supported' >&2; exit 1; }
. /etc/openwrt_release
case "$DISTRIB_RELEASE:$DISTRIB_ARCH:$(uname -m)" in
    *24.10*:aarch64_generic:aarch64|*24.10*:aarch64_cortex-a53:aarch64|*24.10*:aarch64_cortex-a72:aarch64|*24.10*:aarch64_cortex-a76:aarch64) ;;
    *) echo 'Unsupported firmware/package/CPU target; nothing changed' >&2; exit 1 ;;
esac
grep -Eq '^[[:space:]]*option[[:space:]]+check_signature([[:space:]]|$)' /etc/opkg.conf || {
    echo 'Enable opkg signature checking before preparing tools; nothing changed' >&2; exit 1;
}
# The simplified entry never authorizes replacing an existing installation.
if [ "$1" = --apply-fresh ]; then
    for hfgj_pkg in mihomo-hfgj mihomo-meta mihomo-alpha mihomo-hfgj-rollback mihomo nikki luci-app-nikki; do
        hfgj_status=$(opkg status "$hfgj_pkg") || exit 1
        if printf '%s\n' "$hfgj_status" | grep -q '^Status: .* installed$'; then
            echo 'Fresh install refused: existing package detected; use reviewed migration steps' >&2; exit 1
        fi
    done
    for hfgj_path in /usr/bin/mihomo /usr/libexec/mihomo /etc/init.d/nikki /etc/config/nikki /etc/nikki; do
        [ ! -e "$hfgj_path" ] && [ ! -L "$hfgj_path" ] || {
            echo 'Fresh install refused: existing core/service path detected' >&2; exit 1;
        }
    done
fi
prepare_tools() {
    hfgj_stat_usable=0
    if sh "$hfgj_script_dir/rollback-package.sh" --check-stat; then hfgj_stat_usable=1; fi
    if [ "$hfgj_stat_usable" = 0 ] || ! opkg status coreutils-stat | grep -q '^Status: .* installed$'; then
        # Uses the configured signed distro feed; never updates/removes a core.
        opkg install coreutils-stat || { echo 'coreutils-stat installation failed; core/service untouched' >&2; return 1; }
    fi
    opkg status coreutils-stat | grep -q '^Status: .* installed$' || {
        echo 'coreutils-stat is not registered after preparation; core/service untouched' >&2; return 1;
    }
    sh "$hfgj_script_dir/rollback-package.sh" --check-tools
}
if [ "$1" = --prepare-tools ]; then
    prepare_tools
    echo 'Migration tools ready; core/service untouched.'
    exit 0
fi
sh "$hfgj_script_dir/feed.sh"
prepare_tools
hfgj_migrate_action=--apply
[ "$1" != --apply-fresh ] || hfgj_migrate_action=--apply-fresh
sh "$hfgj_script_dir/migrate.sh" "$hfgj_migrate_action"
# Install only after the HFGJ core provides the virtual dependency.
opkg status mihomo-hfgj | grep -q '^Status: .* installed$'
opkg install nikki luci-app-nikki
hfgj_languages=$(opkg list-installed 'luci-i18n-base-*' | awk '{sub(/^luci-i18n-base-/, "", $1); print $1}')
for hfgj_language in $hfgj_languages; do
    case "$hfgj_language" in *[!a-zA-Z0-9_-]*) echo 'Invalid language identifier' >&2; exit 1 ;; esac
    opkg install "luci-i18n-nikki-$hfgj_language"
done
echo 'Installed HFGJ core and official Nikki packages. Check Nikki service status and provider connectivity.'
