#!/bin/sh
# HFGJ feed bootstrap. Public key and signature are verified before activation.
set -eu
umask 077
HFGJ_FEED_URL=${HFGJ_FEED_URL:-https://hfgj.github.io/OpenWrt-nikki}
HFGJ_OFFICIAL_URL=${HFGJ_OFFICIAL_URL:-https://nikkinikki.pages.dev}
case "$HFGJ_FEED_URL $HFGJ_OFFICIAL_URL" in
    *[!a-zA-Z0-9:/._\ -]*) echo 'Feed URLs must be public HTTPS URLs without credentials/query parameters' >&2; exit 1 ;;
esac
case "$HFGJ_FEED_URL" in https://*) ;; *) exit 1 ;; esac
case "$HFGJ_OFFICIAL_URL" in https://*) ;; *) exit 1 ;; esac
[ "$(id -u)" = 0 ] || { echo 'Run on the router as root' >&2; exit 1; }
command -v opkg >/dev/null || { echo 'Initial release supports opkg only; APK is not enabled' >&2; exit 1; }
command -v usign >/dev/null || { echo 'usign is required for feed verification' >&2; exit 1; }
[ -x /sbin/fw4 ] || { echo 'firewall4 is required' >&2; exit 1; }
. /etc/openwrt_release
case "$DISTRIB_RELEASE" in *24.10*) hfgj_branch=openwrt-24.10 ;; *) echo "Unsupported firmware release: $DISTRIB_RELEASE" >&2; exit 1 ;; esac
case "$DISTRIB_ARCH" in aarch64_generic|aarch64_cortex-a53|aarch64_cortex-a72|aarch64_cortex-a76) ;; *) echo "Unsupported package architecture: $DISTRIB_ARCH" >&2; exit 1 ;; esac
grep -Eq '^[[:space:]]*option[[:space:]]+check_signature([[:space:]]|$)' /etc/opkg.conf || {
    echo 'Enable opkg signature checking before using this signed feed; this script does not change global policy' >&2; exit 1;
}
hfgj_work=$(mktemp -d /tmp/hfgj-feed.XXXXXX)
trap 'rm -rf "$hfgj_work"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
hfgj_core_feed="${HFGJ_FEED_URL%/}/$hfgj_branch/$DISTRIB_ARCH/hfgj"
wget -q -O "$hfgj_work/key-build.pub" "${HFGJ_FEED_URL%/}/key-build.pub"
wget -q -O "$hfgj_work/Packages" "$hfgj_core_feed/Packages"
wget -q -O "$hfgj_work/Packages.sig" "$hfgj_core_feed/Packages.sig"
usign -V -m "$hfgj_work/Packages" -p "$hfgj_work/key-build.pub" -x "$hfgj_work/Packages.sig"
grep -qx 'Package: mihomo-hfgj' "$hfgj_work/Packages" || { echo 'HFGJ core missing from signed index' >&2; exit 1; }
if [ -n "${HFGJ_KEY_SHA256:-}" ]; then
    [ "$(sha256sum "$hfgj_work/key-build.pub" | cut -d ' ' -f 1)" = "$HFGJ_KEY_SHA256" ] || { echo 'Public key fingerprint mismatch' >&2; exit 1; }
fi
# Refuse an unrelated configuration using our reserved feed name.
for hfgj_conf in /etc/opkg.conf /etc/opkg/*.conf; do
    [ -f "$hfgj_conf" ] || continue
    [ "$hfgj_conf" = /etc/opkg/hfgj-core.conf ] && continue
    if awk '$1 ~ /^src/ && $2 == "hfgj-core" {found=1} END {exit !found}' "$hfgj_conf"; then
        echo 'hfgj-core already exists in another config; review it before migration' >&2; exit 1
    fi
done
if ! grep -Eq '^[[:space:]]*src(/gz)?[[:space:]]+nikki[[:space:]]' /etc/opkg.conf /etc/opkg/*.conf; then
    wget -q -O "$hfgj_work/official.pub" "${HFGJ_OFFICIAL_URL%/}/key-build.pub"
    opkg-key add "$hfgj_work/official.pub"
    printf 'src/gz nikki %s/%s/%s/nikki\n' "${HFGJ_OFFICIAL_URL%/}" "$hfgj_branch" "$DISTRIB_ARCH" > "$hfgj_work/nikki.conf"
    chmod 0644 "$hfgj_work/nikki.conf"
    cp "$hfgj_work/nikki.conf" /etc/opkg/hfgj-nikki.conf
fi
opkg-key add "$hfgj_work/key-build.pub"
printf 'src/gz hfgj-core %s\n' "$hfgj_core_feed" > "$hfgj_work/core.conf"
chmod 0644 "$hfgj_work/core.conf"
cp "$hfgj_work/core.conf" /etc/opkg/hfgj-core.conf
opkg update
echo 'HFGJ core feed configured. Nikki/LuCI continue using the official feed.'
