#!/bin/sh
# opkg owns the replacement. No --force-depends or database edits are used.
set -eu
umask 077
hfgj_action=${1:---plan}
case "$hfgj_action" in --plan|--apply|--rollback) ;; *) echo 'Usage: sh migrate.sh --plan|--apply|--rollback BACKUP_DIR' >&2; exit 1 ;; esac
command -v opkg >/dev/null || { echo 'Only opkg is supported initially' >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo 'Run as root' >&2; exit 1; }
. /etc/openwrt_release
case "$DISTRIB_RELEASE:$DISTRIB_ARCH:$(uname -m)" in
    *24.10*:aarch64_generic:aarch64|*24.10*:aarch64_cortex-a53:aarch64|*24.10*:aarch64_cortex-a72:aarch64|*24.10*:aarch64_cortex-a76:aarch64) ;;
    *) echo 'Unsupported firmware/package/CPU target; nothing changed' >&2; exit 1 ;;
esac

installed() { opkg status "$1" 2>/dev/null | grep -q '^Status: .* installed$'; }
pkg_field() { opkg status "$1" 2>/dev/null | sed -n "s/^$2: //p"; }
core_path() {
    [ -e /usr/bin/mihomo ] || [ -L /usr/bin/mihomo ] || return 0
    readlink -f /usr/bin/mihomo 2>/dev/null || true
}
service_running() { [ -x /etc/init.d/nikki ] && /etc/init.d/nikki running >/dev/null 2>&1; }
# OpenWrt SDK IPKs are gzip/tar containers. Reject unsupported formats before stopping.
ipk_part() {
    tar -xzOf "$1" "./$2" 2>/dev/null || tar -xzOf "$1" "$2"
}
ipk_field() {
    ipk_part "$1" control.tar.gz | { tar -xzOf - ./control 2>/dev/null || true; } | sed -n "s/^$2: //p"
}
ipk_payload() {
    ipk_part "$1" data.tar.gz | tar -xzOf - "./$2"
}
sha() { sha256sum "$1" | cut -d ' ' -f 1; }
check_space() {
    hfgj_available=$(df -Pk "$1" | awk 'END {print $4}')
    case "$hfgj_available" in ''|*[!0-9]*) echo 'Cannot determine free disk space' >&2; return 1 ;; esac
    [ "$hfgj_available" -ge "$2" ] || {
        echo "Insufficient free space at $1: need at least $2 KiB; available $hfgj_available KiB. Service has not been stopped." >&2
        return 1
    }
}
record_package() {
    hfgj_pkg=$1
    hfgj_expected_version=$2
    opkg download "$hfgj_pkg"
    hfgj_downloaded=
    for hfgj_ipk in ./*.ipk; do
        [ -f "$hfgj_ipk" ] || continue
        if [ "$(ipk_field "$hfgj_ipk" Package)" = "$hfgj_pkg" ] && [ "$(ipk_field "$hfgj_ipk" Version)" = "$hfgj_expected_version" ]; then
            [ -z "$hfgj_downloaded" ] || { echo 'Ambiguous package download' >&2; return 1; }
            hfgj_downloaded=$hfgj_ipk
        fi
    done
    [ -n "$hfgj_downloaded" ] || { echo "Exact rollback/candidate package unavailable: $hfgj_pkg $hfgj_expected_version; service has not been stopped" >&2; return 1; }
    hfgj_trusted=0
    hfgj_actual_sha=$(sha "$hfgj_downloaded")
    for hfgj_list in /var/opkg-lists/*; do
        [ -f "$hfgj_list.sig" ] || continue
        hfgj_signed_sha=$(awk -v name="$hfgj_pkg" -v version="$hfgj_expected_version" '
            BEGIN {RS=""; FS="\n"}
            {p=""; v=""; s=""; for(i=1;i<=NF;i++) {
                if($i~/^Package: /) p=substr($i,10)
                if($i~/^Version: /) v=substr($i,10)
                if($i~/^SHA256sum: /) s=substr($i,12)
            } if(p==name && v==version) print s}' "$hfgj_list")
        [ "$hfgj_signed_sha" = "$hfgj_actual_sha" ] || continue
        if usign -V -m "$hfgj_list" -x "$hfgj_list.sig" -P /etc/opkg/keys; then hfgj_trusted=1; break; fi
    done
    [ "$hfgj_trusted" = 1 ] || { echo "Downloaded package does not match a trusted signed index: $hfgj_pkg" >&2; return 1; }
    # opkg may already use the desired basename. GNU/BusyBox mv can reject
    # a same-file rename, although macOS mv accepts it.
    if [ "${hfgj_downloaded#./}" != "$hfgj_pkg.ipk" ]; then
        mv "$hfgj_downloaded" "$hfgj_pkg.ipk"
    fi
}
validate_hfgj_package() {
    [ "$(ipk_field "$1" Package)" = mihomo-hfgj ]
    [ "$(ipk_field "$1" Architecture)" = "$DISTRIB_ARCH" ]
    [ "$(ipk_field "$1" Provides)" = mihomo ]
    [ "$(ipk_field "$1" Alternatives)" = '300:/usr/bin/mihomo:/usr/libexec/mihomo' ]
    command -v jsonfilter >/dev/null || { echo 'jsonfilter is required' >&2; return 1; }
    ipk_payload "$1" usr/share/mihomo-hfgj/core.json > candidate.json
    ipk_payload "$1" usr/libexec/mihomo > candidate
    chmod 0755 candidate
    hfgj_expected_core=$(jsonfilter -i candidate.json -e '@.core_version')
    case "$hfgj_expected_core" in v*-hfgj.*) ;; *) echo 'Not an HFGJ stable core' >&2; return 1 ;; esac
    hfgj_expected_sha=$(jsonfilter -i candidate.json -e '@.binary_sha256')
    [ "$(sha candidate)" = "$hfgj_expected_sha" ] || { echo 'Candidate binary hash mismatch' >&2; return 1; }
    ./candidate -v > candidate.version
    grep -F "Mihomo Meta $hfgj_expected_core linux arm64" candidate.version >/dev/null
}
verify_installed() {
    installed mihomo-hfgj || return 1
    [ "$(core_path)" = /usr/libexec/mihomo ] || return 1
    [ "$(sha /usr/libexec/mihomo)" = "$hfgj_expected_sha" ]
}
rollback() {
    # A saved package is required, not just an unregistered binary restore.
    hfgj_backup=$1
    [ -d "$hfgj_backup" ] && [ -f "$hfgj_backup/state" ] || { echo 'Invalid backup directory' >&2; return 1; }
    cd "$hfgj_backup"
    hfgj_old_package=$(sed -n 's/^old_package=//p' state)
    hfgj_old_version=$(sed -n 's/^old_version=//p' state)
    hfgj_was_running=$(sed -n 's/^was_running=//p' state)
    case "$hfgj_old_package" in mihomo-meta|mihomo-alpha|mihomo-hfgj) ;; *) echo 'No previous registered core to roll back to' >&2; return 1 ;; esac
    [ "$(sha "$hfgj_old_package.ipk")" = "$(sed -n 's/^old_ipk_sha256=//p' state)" ] || return 1
    [ "$(ipk_field "$hfgj_old_package.ipk" Package)" = "$hfgj_old_package" ] || return 1
    [ "$(ipk_field "$hfgj_old_package.ipk" Version)" = "$hfgj_old_version" ] || return 1
    [ "$(sha core.binary)" = "$(sed -n 's/^old_binary_sha256=//p' state)" ] || return 1
    case "$hfgj_was_running" in 0|1) ;; *) return 1 ;; esac
    if [ -x /etc/init.d/nikki ]; then /etc/init.d/nikki stop || return 1; fi
    if [ "$hfgj_old_package" != mihomo-hfgj ] && installed mihomo-hfgj; then
        # Official packages do not declare Replaces: mihomo-hfgj. Install a
        # temporary dependency provider during the inverse transition.
        [ -f rollback-provider.ipk ] || { echo 'Rollback provider is missing; original IPK/binary are saved. Do not force dependency removal.' >&2; return 1; }
        [ "$(sha rollback-provider.ipk)" = "$(sed -n 's/^bridge_ipk_sha256=//p' state)" ] || return 1
        opkg install ./rollback-provider.ipk || return 1
        opkg remove mihomo-hfgj || return 1
    fi
    opkg install --force-downgrade --force-reinstall "./$hfgj_old_package.ipk" || return 1
    if installed mihomo-hfgj-rollback; then opkg remove mihomo-hfgj-rollback || return 1; fi
    # Restore the actual previous bytes if a core updater changed the packaged binary.
    hfgj_restore_path=$(core_path)
    [ "$hfgj_restore_path" = /usr/libexec/mihomo ] || { echo 'Unexpected restored core path' >&2; return 1; }
    cp core.binary /usr/libexec/mihomo.hfgj-restore || return 1
    chmod 0755 /usr/libexec/mihomo.hfgj-restore || return 1
    mv /usr/libexec/mihomo.hfgj-restore /usr/libexec/mihomo || return 1
    [ "$(sha /usr/libexec/mihomo)" = "$(sed -n 's/^old_binary_sha256=//p' state)" ] || return 1
    if [ "$hfgj_was_running" = 1 ]; then /etc/init.d/nikki start || return 1; fi
    echo 'Previous core package and binary restored. Configuration snapshots were not overwritten.'
}

if [ "$hfgj_action" = --rollback ]; then
    [ $# = 2 ] || { echo 'Supply a backup directory' >&2; exit 1; }
    rollback "$2"
    exit
fi
hfgj_old_package=
for hfgj_pkg in mihomo-hfgj mihomo-meta mihomo-alpha; do
    if installed "$hfgj_pkg"; then
        [ -z "$hfgj_old_package" ] || { echo 'Multiple core packages installed; review manually' >&2; exit 1; }
        hfgj_old_package=$hfgj_pkg
    fi
done
hfgj_current_path=$(core_path)
if [ -n "$hfgj_current_path" ] && [ "$hfgj_current_path" != /usr/libexec/mihomo ]; then
    echo 'Core entry is not the registered alternatives layout; repair package state before migration' >&2; exit 1
fi
if [ -n "$hfgj_current_path" ] && [ -z "$hfgj_old_package" ]; then
    echo 'Unregistered core found; repair package ownership first' >&2; exit 1
fi
printf 'Target: %s / %s; old core package: %s; replacement: mihomo-hfgj\n' "$DISTRIB_RELEASE" "$DISTRIB_ARCH" "${hfgj_old_package:-none}"
if [ "$hfgj_action" = --plan ]; then
    echo 'Plan only: verify signed candidate and exact rollback IPK; backup; stop Nikki; let opkg replace the package; verify/start. No changes made.'
    exit
fi
if [ "$hfgj_old_package" = mihomo-hfgj ]; then
    echo 'Already using mihomo-hfgj. Update this registered package through LuCI/opkg.'
    exit
fi
[ -r /var/opkg-lists/hfgj-core ] && [ -r /var/opkg-lists/hfgj-core.sig ] || { echo 'Run the HFGJ feed bootstrap first' >&2; exit 1; }
usign -V -m /var/opkg-lists/hfgj-core -x /var/opkg-lists/hfgj-core.sig -P /etc/opkg/keys
hfgj_candidate_version=$(awk 'BEGIN {RS=""; FS="\n"} /(^|\n)Package: mihomo-hfgj(\n|$)/ {for(i=1;i<=NF;i++) if($i~/^Version: /) print substr($i,10)}' /var/opkg-lists/hfgj-core)
hfgj_candidate_sha=$(awk 'BEGIN {RS=""; FS="\n"} /(^|\n)Package: mihomo-hfgj(\n|$)/ {for(i=1;i<=NF;i++) if($i~/^SHA256sum: /) print substr($i,12)}' /var/opkg-lists/hfgj-core)
hfgj_bridge_version=$(awk 'BEGIN {RS=""; FS="\n"} /(^|\n)Package: mihomo-hfgj-rollback(\n|$)/ {for(i=1;i<=NF;i++) if($i~/^Version: /) print substr($i,10)}' /var/opkg-lists/hfgj-core)
hfgj_bridge_sha=$(awk 'BEGIN {RS=""; FS="\n"} /(^|\n)Package: mihomo-hfgj-rollback(\n|$)/ {for(i=1;i<=NF;i++) if($i~/^SHA256sum: /) print substr($i,12)}' /var/opkg-lists/hfgj-core)
[ -n "$hfgj_candidate_version" ] && [ "${#hfgj_candidate_sha}" = 64 ] || { echo 'Invalid signed candidate index' >&2; exit 1; }
mkdir -p /root/hfgj-core-backups
chmod 0700 /root/hfgj-core-backups
hfgj_existing_kib=0
if [ -f /usr/libexec/mihomo ]; then hfgj_existing_kib=$(du -k /usr/libexec/mihomo | awk '{print $1}'); fi
hfgj_config_kib=0
if [ -d /etc/nikki ]; then hfgj_config_kib=$(du -sk /etc/nikki | awk '{print $1}'); fi
# Two copies of old bytes/config plus candidate extraction, IPKs and opkg staging.
check_space /root/hfgj-core-backups "$((hfgj_existing_kib * 2 + hfgj_config_kib * 2 + 131072))"
check_space /usr/libexec 131072
hfgj_backup=$(mktemp -d /root/hfgj-core-backups/migration.XXXXXX)
cd "$hfgj_backup"
record_package mihomo-hfgj "$hfgj_candidate_version"
[ "$(sha mihomo-hfgj.ipk)" = "$hfgj_candidate_sha" ] || { echo 'Candidate IPK differs from signed feed index' >&2; exit 1; }
validate_hfgj_package ./mihomo-hfgj.ipk
hfgj_was_running=0
if service_running; then hfgj_was_running=1; fi
hfgj_old_version=
if [ -n "$hfgj_old_package" ]; then
    hfgj_old_version=$(pkg_field "$hfgj_old_package" Version)
    record_package "$hfgj_old_package" "$hfgj_old_version"
    cp /usr/libexec/mihomo core.binary
    # The small helper keeps Nikki's virtual dependency satisfied on rollback.
    record_package mihomo-hfgj-rollback "$hfgj_bridge_version"
    [ "$(sha mihomo-hfgj-rollback.ipk)" = "$hfgj_bridge_sha" ]
    mv mihomo-hfgj-rollback.ipk rollback-provider.ipk
    [ "$(ipk_field rollback-provider.ipk Package)" = mihomo-hfgj-rollback ]
    [ "$(ipk_field rollback-provider.ipk Provides)" = mihomo ]
    hfgj_old_sha=$(sha core.binary)
    hfgj_old_ipk_sha=$(sha "$hfgj_old_package.ipk")
else
    hfgj_old_sha=
    hfgj_old_ipk_sha=
fi
printf 'old_package=%s\nold_version=%s\nwas_running=%s\nold_binary_sha256=%s\nold_ipk_sha256=%s\nbridge_ipk_sha256=%s\n' "$hfgj_old_package" "$hfgj_old_version" "$hfgj_was_running" "$hfgj_old_sha" "$hfgj_old_ipk_sha" "$hfgj_bridge_sha" > state
cp /usr/lib/opkg/status opkg-status.snapshot
if [ -f /etc/config/nikki ]; then cp /etc/config/nikki nikki.uci.snapshot; fi
if [ -d /etc/nikki ]; then tar -czf nikki.private.snapshot.tar.gz -C /etc nikki; fi
hfgj_transaction_active=1
finish_transaction() {
    hfgj_exit=$?
    trap - EXIT HUP INT TERM
    if [ "$hfgj_transaction_active" = 1 ] && [ "$hfgj_exit" != 0 ]; then
        echo "Migration failed; backup: $hfgj_backup" >&2
        if [ -n "$hfgj_old_package" ]; then
            if ! rollback "$hfgj_backup"; then
                echo 'Automatic rollback failed. Saved IPKs/binary remain in the backup directory; manual recovery is required.' >&2
            fi
        else
            echo 'First-install attempt failed; no previous core package exists. Inspect the package/service state before retrying.' >&2
        fi
    fi
    exit "$hfgj_exit"
}
trap finish_transaction EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
if [ -x /etc/init.d/nikki ]; then /etc/init.d/nikki stop; fi
hfgj_failed=0
if [ -n "$hfgj_old_package" ]; then
    opkg install ./rollback-provider.ipk || hfgj_failed=1
    if [ "$hfgj_failed" = 0 ]; then opkg remove "$hfgj_old_package" || hfgj_failed=1; fi
fi
if [ "$hfgj_failed" = 0 ]; then opkg install ./mihomo-hfgj.ipk || hfgj_failed=1; fi
if [ "$hfgj_failed" = 0 ] && installed mihomo-hfgj-rollback; then
    opkg remove mihomo-hfgj-rollback || hfgj_failed=1
fi
if [ "$hfgj_failed" = 0 ]; then verify_installed || hfgj_failed=1; fi
if [ "$hfgj_failed" = 0 ] && [ "$hfgj_was_running" = 1 ]; then
    /etc/init.d/nikki start || hfgj_failed=1
    if [ "$hfgj_failed" = 0 ]; then sleep 2; service_running || hfgj_failed=1; fi
fi
if [ "$hfgj_failed" != 0 ]; then
    exit 1
fi
hfgj_transaction_active=0
printf 'Migrated core package. Backup: %s\n' "$hfgj_backup"
echo 'Check provider loading and connectivity next; service status alone is not functional validation.'
