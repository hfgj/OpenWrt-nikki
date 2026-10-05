#!/bin/sh
# opkg owns the replacement. No --force-depends or database edits are used.
set -eu
umask 077
HFGJ_BACKUP_DIR=${HFGJ_BACKUP_DIR:-/root/hfgj-core-backups}
HFGJ_WORK_DIR=${HFGJ_WORK_DIR:-$HFGJ_BACKUP_DIR/work}
HFGJ_STORAGE_MOUNT=${HFGJ_STORAGE_MOUNT:-}
# All opkg extraction/staging goes to the explicit persistent workspace.
opkg_work() { opkg --tmp-dir "$hfgj_work/opkg-tmp" "$@"; }
hfgj_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
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
sha() { hfgj_sum=$(sha256sum "$1") || return 1; printf '%s\n' "${hfgj_sum%% *}"; }
# opkg may retain src/gz lists as compressed bytes; signatures cover plaintext.
trusted_index() {
    hfgj_index_copy=$(mktemp /tmp/hfgj-signed-index.XXXXXX) || return 1
    if gzip -t "$1" 2>/dev/null; then
        gzip -dc "$1" > "$hfgj_index_copy" || { rm -f "$hfgj_index_copy"; return 1; }
    else
        cp "$1" "$hfgj_index_copy" || { rm -f "$hfgj_index_copy"; return 1; }
    fi
    if ! usign -V -m "$hfgj_index_copy" -x "$1.sig" -P /etc/opkg/keys >/dev/null; then
        rm -f "$hfgj_index_copy"
        return 1
    fi
    printf '%s\n' "$hfgj_index_copy"
}
free_kib() { df -Pk "$1" | awk 'END {print $4}'; }
filesystem() { df -Pk "$1" | awk 'END {print $1}'; }
check_space() {
    hfgj_available=$(free_kib "$1")
    case "$hfgj_available" in ''|*[!0-9]*) echo 'Cannot determine free disk space' >&2; return 1 ;; esac
    [ "$hfgj_available" -ge "$2" ] || {
        echo "Insufficient free space at $1: need at least $2 KiB; available $hfgj_available KiB." >&2
        return 1
    }
}
validate_path() {
    case "$1" in /*) ;; *) echo 'Storage paths must be absolute' >&2; return 1 ;; esac
    case "$1" in /|*[!a-zA-Z0-9/._-]*|*/../*|*/..|*/./*) echo 'Unsafe storage path' >&2; return 1 ;; esac
}
check_mount() {
    [ -n "$HFGJ_STORAGE_MOUNT" ] || return 0
    validate_path "$HFGJ_STORAGE_MOUNT"
    awk -v path="$HFGJ_STORAGE_MOUNT" '$2==path && $3!="tmpfs" && $3!="ramfs" && $3!="squashfs" && $4 ~ /(^|,)rw(,|$)/ {ok=1} END {exit !ok}' /proc/mounts || {
        echo 'Required persistent storage mount is absent or not writable; no fallback to overlay' >&2; return 1;
    }
    for hfgj_path in "$HFGJ_BACKUP_DIR" "$HFGJ_WORK_DIR"; do
        case "$hfgj_path" in "$HFGJ_STORAGE_MOUNT"/*) ;; *) echo 'Backup/work paths must be under the required mount' >&2; return 1 ;; esac
    done
}
prepare_storage() {
    validate_path "$HFGJ_BACKUP_DIR"
    validate_path "$HFGJ_WORK_DIR"
    check_mount
    mkdir -p "$HFGJ_BACKUP_DIR" "$HFGJ_WORK_DIR"
    HFGJ_BACKUP_DIR=$(cd "$HFGJ_BACKUP_DIR" && pwd -P)
    HFGJ_WORK_DIR=$(cd "$HFGJ_WORK_DIR" && pwd -P)
    check_mount
    chmod 0700 "$HFGJ_BACKUP_DIR" "$HFGJ_WORK_DIR"
    for hfgj_path in "$HFGJ_BACKUP_DIR" "$HFGJ_WORK_DIR"; do
        case "$(filesystem "$hfgj_path")" in tmpfs|ramfs) echo 'Backup/work directories must be persistent' >&2; return 1 ;; esac
    done
}
bytes_kib() { hfgj_bytes=$(wc -c < "$1"); echo "$(((hfgj_bytes + 1023) / 1024))"; }
reclaimable_core_kib() {
    [ -f /usr/libexec/mihomo ] || { echo 0; return; }
    case "$(filesystem /usr/libexec)" in
        overlay*)
            # A lower squashfs file cannot free overlay blocks when removed.
            [ -f /overlay/upper/usr/libexec/mihomo ] || { echo 0; return; } ;;
    esac
    du -k /usr/libexec/mihomo | awk '{print $1}'
}
check_core_peak() {
    check_space /usr/libexec "$((hfgj_install_need_kib + 8192))" || return 1
    hfgj_available=$(free_kib /usr/libexec)
    hfgj_reclaim=$(reclaimable_core_kib)
    [ -n "$hfgj_available" ] && [ -n "$hfgj_reclaim" ] || return 1
    case "$hfgj_available:$hfgj_reclaim" in *[!0-9:]*) return 1 ;; esac
    [ "$((hfgj_available + hfgj_reclaim))" -ge "$hfgj_core_peak_kib" ] || {
        echo "Insufficient core filesystem space after stop: available $hfgj_available KiB + removable core $hfgj_reclaim KiB; peak requires $hfgj_core_peak_kib KiB" >&2
        return 1
    }
}
record_package() {
    hfgj_pkg=$1
    hfgj_expected_version=$2
    for hfgj_list in /var/opkg-lists/*; do
        [ -f "$hfgj_list.sig" ] || continue
        hfgj_checked_index=$(trusted_index "$hfgj_list") || continue
        if hfgj_signed_record=$(awk -v name="$hfgj_pkg" -v version="$hfgj_expected_version" '
            BEGIN {RS=""; FS="\n"}
            {p=""; v=""; f=""; s=""; for(i=1;i<=NF;i++) {
                if($i~/^Package: /) p=substr($i,10)
                if($i~/^Version: /) v=substr($i,10)
                if($i~/^Filename: /) f=substr($i,11)
                if($i~/^SHA256sum: /) s=substr($i,12)
            } if(p==name && v==version) {count++; filename=f; sum=s}}
            END {if(count>1) exit 1; if(count==1) print filename "\n" sum}
        ' "$hfgj_checked_index"); then
            rm -f "$hfgj_checked_index"
        else
            rm -f "$hfgj_checked_index"
            echo 'Ambiguous exact package in signed index' >&2
            return 1
        fi
        [ -n "$hfgj_signed_record" ] || continue
        hfgj_filename=$(printf '%s\n' "$hfgj_signed_record" | sed -n '1p')
        hfgj_signed_sha=$(printf '%s\n' "$hfgj_signed_record" | sed -n '2p')
        case "$hfgj_filename" in ''|.*|*[!a-zA-Z0-9_.+-]*) echo 'Unsupported signed filename' >&2; return 1 ;; esac
        case "$hfgj_filename" in *.ipk) ;; *) return 1 ;; esac
        case "$hfgj_signed_sha" in *[!0-9a-f]*) return 1 ;; esac
        [ "${#hfgj_signed_sha}" = 64 ] || return 1
        hfgj_feed_name=$(basename "$hfgj_list")
        hfgj_source_url=$(
            for hfgj_conf in /etc/opkg.conf /etc/opkg/*.conf; do
                [ -f "$hfgj_conf" ] || continue
                awk -v name="$hfgj_feed_name" '$1 ~ /^src(\/gz)?$/ && $2==name {print $3}' "$hfgj_conf"
            done
        )
        # Supported feeds are public HTTPS URLs. No query/credentials/path tricks.
        case "$hfgj_source_url" in https://*) ;; *) echo 'Exact package feed URL unavailable' >&2; return 1 ;; esac
        case "$hfgj_source_url" in *[!a-zA-Z0-9:/._-]*) echo 'Unsupported or ambiguous feed URL' >&2; return 1 ;; esac
        hfgj_downloaded="./$hfgj_pkg.download.ipk"
        wget -q -O "$hfgj_downloaded" "${hfgj_source_url%/}/$hfgj_filename" || return 1
        [ "$(sha "$hfgj_downloaded")" = "$hfgj_signed_sha" ] || { echo 'Exact download differs from signed hash' >&2; return 1; }
        [ "$(ipk_field "$hfgj_downloaded" Package)" = "$hfgj_pkg" ] || return 1
        [ "$(ipk_field "$hfgj_downloaded" Version)" = "$hfgj_expected_version" ] || return 1
        mv "$hfgj_downloaded" "./$hfgj_pkg.ipk"
        return 0
    done
    echo "Exact signed package unavailable: $hfgj_pkg $hfgj_expected_version; service untouched" >&2
    return 1
}
validate_hfgj_package() {
    [ "$(ipk_field "$1" Package)" = mihomo-hfgj ]
    [ "$(ipk_field "$1" Architecture)" = "$DISTRIB_ARCH" ]
    [ "$(ipk_field "$1" Provides)" = mihomo ]
    ipk_field "$1" Depends | tr ',' '\n' | grep -Eq '^[[:space:]]*coreutils-stat([[:space:]]|$)' || {
        echo 'Candidate lacks coreutils-stat dependency; service untouched' >&2; return 1;
    }
    [ "$(ipk_field "$1" Alternatives)" = '300:/usr/bin/mihomo:/usr/libexec/mihomo' ]
    for hfgj_replacement_field in Conflicts Replaces; do
        hfgj_replacement=$(ipk_field "$1" "$hfgj_replacement_field" | tr ', ' '\n' | sed '/^$/d' | sort)
        [ "$hfgj_replacement" = "$(printf 'mihomo-alpha\nmihomo-hfgj-rollback\nmihomo-meta')" ] || {
            echo 'Candidate lacks the exact normal replacement contract' >&2; return 1;
        }
    done
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
    [ "$(pkg_field mihomo-hfgj Version)" = "$hfgj_candidate_version" ] || return 1
    for hfgj_legacy in mihomo-meta mihomo-alpha mihomo-hfgj-rollback; do ! installed "$hfgj_legacy" || return 1; done
    [ "$(core_path)" = /usr/libexec/mihomo ] || return 1
    [ "$(sha /usr/libexec/mihomo)" = "$hfgj_expected_sha" ]
}
stage() {
    printf 'phase=%s\n' "$1" > "$hfgj_backup/transaction.tmp" &&
        mv "$hfgj_backup/transaction.tmp" "$hfgj_backup/transaction"
}
stop_service() {
    # procd stop may fail when the service is already absent. Do not stop twice.
    if service_running; then
        /etc/init.d/nikki stop || return 1
        ! service_running || return 1
    fi
}
restore_service() {
    if [ "$hfgj_was_running" = 1 ]; then
        if ! service_running; then /etc/init.d/nikki start || return 1; fi
        service_running || return 1
    else
        stop_service || return 1
    fi
}
unchanged_original() {
    installed "$hfgj_old_package" &&
        [ "$(pkg_field "$hfgj_old_package" Version)" = "$hfgj_old_version" ] &&
        [ "$(core_path)" = /usr/libexec/mihomo ] &&
        [ "$(sha /usr/libexec/mihomo)" = "$hfgj_old_sha" ] || return 1
    for hfgj_other in mihomo-meta mihomo-alpha mihomo-hfgj; do
        [ "$hfgj_other" = "$hfgj_old_package" ] && continue
        ! installed "$hfgj_other" || return 1
    done
}
installed_kib() {
    hfgj_size=$(ipk_field "$1" Installed-Size)
    case "$hfgj_size" in ''|*[!0-9]*) echo 'Invalid IPK Installed-Size' >&2; return 1 ;; esac
    [ "$hfgj_size" -gt 0 ] || return 1
    printf '%s\n' "$(((hfgj_size + 1023) / 1024))"
}
# After a failed recovery, start only a registered core whose bytes are known.
start_known_core() {
    [ "$hfgj_resume_running" = 1 ] || return 0
    [ "$(core_path)" = /usr/libexec/mihomo ] || return 1
    hfgj_known_package=0
    for hfgj_known in mihomo-meta mihomo-alpha mihomo-hfgj; do
        if installed "$hfgj_known"; then hfgj_known_package=$((hfgj_known_package + 1)); fi
    done
    [ "$hfgj_known_package" = 1 ] || return 1
    hfgj_current_sha=$(sha /usr/libexec/mihomo) || return 1
    if [ "$hfgj_current_sha" != "$hfgj_old_sha" ] &&
       [ "$hfgj_current_sha" != "$hfgj_packaged_old_sha" ] &&
       [ "$hfgj_current_sha" != "$hfgj_saved_candidate_sha" ]; then return 1; fi
    if ! service_running; then /etc/init.d/nikki start || return 1; fi
    service_running
}
rollback_packages() {
    stage restoring-package || return 1
    # Controlled version downgrade permits same-name/same-version damage repair.
    # This flag does not bypass dependency checks (unlike force-reinstall).
    opkg_work install --force-downgrade ./recovery.ipk || return 1
    installed "$hfgj_old_package" || return 1
    [ "$(pkg_field "$hfgj_old_package" Version)" = "$hfgj_old_version~hfgjrestore" ] || return 1
    [ "$(core_path)" = /usr/libexec/mihomo ] || return 1
    [ "$(sha /usr/libexec/mihomo)" = "$hfgj_packaged_old_sha" ] || return 1
    stage restoring-exact-package || return 1
    # Higher than ~hfgjrestore: normal upgrade returns to untouched official metadata.
    opkg_work install "./$hfgj_old_package.ipk" || return 1
    installed "$hfgj_old_package" || return 1
    [ "$(pkg_field "$hfgj_old_package" Version)" = "$hfgj_old_version" ] || return 1
    for hfgj_other in mihomo-meta mihomo-alpha mihomo-hfgj mihomo-hfgj-rollback; do
        [ "$hfgj_other" = "$hfgj_old_package" ] && continue
        ! installed "$hfgj_other" || return 1
    done
    [ "$(core_path)" = /usr/libexec/mihomo ] || return 1
    [ "$(sha /usr/libexec/mihomo)" = "$hfgj_packaged_old_sha" ] || return 1
    stage restoring-binary || return 1
    if [ "$hfgj_old_sha" != "$hfgj_packaged_old_sha" ]; then
        cp core.binary /usr/libexec/mihomo.hfgj-restore || return 1
        chmod 0755 /usr/libexec/mihomo.hfgj-restore || return 1
        mv /usr/libexec/mihomo.hfgj-restore /usr/libexec/mihomo || return 1
    fi
    unchanged_original || return 1
    stage restoring-service || return 1
    restore_service || return 1
    stage rolled-back || return 1
}
rollback() {
    hfgj_backup=$1
    [ -d "$hfgj_backup" ] && [ -f "$hfgj_backup/state" ] || { echo 'Invalid backup directory' >&2; return 1; }
    cd "$hfgj_backup" || return 1
    hfgj_backup=$(pwd -P)
    hfgj_old_package=$(sed -n 's/^old_package=//p' state)
    hfgj_old_version=$(sed -n 's/^old_version=//p' state)
    hfgj_was_running=$(sed -n 's/^was_running=//p' state)
    hfgj_old_sha=$(sed -n 's/^old_binary_sha256=//p' state)
    case "$hfgj_old_sha" in *[!0-9a-f]*) return 1 ;; esac
    [ "${#hfgj_old_sha}" = 64 ] || return 1
    case "$hfgj_old_package" in mihomo-meta|mihomo-alpha|mihomo-hfgj) ;; *) echo 'No previous registered core to restore' >&2; return 1 ;; esac
    case "$hfgj_was_running" in 0|1) ;; *) return 1 ;; esac
    [ "$(sha "$hfgj_old_package.ipk")" = "$(sed -n 's/^old_ipk_sha256=//p' state)" ] || return 1
    [ "$(ipk_field "$hfgj_old_package.ipk" Package)" = "$hfgj_old_package" ] || return 1
    [ "$(ipk_field "$hfgj_old_package.ipk" Version)" = "$hfgj_old_version" ] || return 1
    [ "$(sha core.binary)" = "$hfgj_old_sha" ] || return 1
    # Compatible with old backups only if the package AND actual bytes are unchanged.
    if unchanged_original; then
        stage recovering-unchanged || return 1
        restore_service || return 1
        stage rolled-back-unchanged || return 1
        echo 'Original package/binary unchanged; previous service state restored.'
        return 0
    fi
    [ "$(sed -n 's/^recovery_protocol=//p' state)" = 2 ] || {
        echo 'Legacy backup cannot restore a changed core with this helper; service untouched' >&2; return 1;
    }
    hfgj_work=$(sed -n 's/^work_dir=//p' state)
    validate_path "$hfgj_work" || return 1
    HFGJ_STORAGE_MOUNT=$(sed -n 's/^storage_mount=//p' state)
    HFGJ_BACKUP_DIR=$hfgj_backup
    HFGJ_WORK_DIR=$hfgj_work
    check_mount || return 1
    mkdir -p "$hfgj_work/opkg-tmp" || return 1
    case "$(filesystem "$hfgj_work")" in tmpfs|ramfs) return 1 ;; esac
    [ "$(sha recovery.ipk)" = "$(sed -n 's/^recovery_ipk_sha256=//p' state)" ] || return 1
    [ "$(sha rollback-package.sh)" = "$(sed -n 's/^recovery_helper_sha256=//p' state)" ] || return 1
    sh ./rollback-package.sh --verify "./$hfgj_old_package.ipk" ./recovery.ipk "$hfgj_work" || return 1
    [ "$(ipk_field recovery.ipk Package)" = "$hfgj_old_package" ] || return 1
    [ "$(ipk_field recovery.ipk Version)" = "$hfgj_old_version~hfgjrestore" ] || return 1
    opkg_work compare-versions "$hfgj_old_version~hfgjrestore" '<' "$hfgj_old_version" || return 1
    ipk_payload "$hfgj_old_package.ipk" usr/libexec/mihomo > "$hfgj_work/rollback-core" || return 1
    hfgj_packaged_old_sha=$(sha "$hfgj_work/rollback-core") || return 1
    hfgj_saved_candidate_sha=$(sed -n 's/^candidate_binary_sha256=//p' state)
    hfgj_package_kib=$(bytes_kib "$hfgj_work/rollback-core") || return 1
    hfgj_actual_kib=$(bytes_kib core.binary) || return 1
    hfgj_core_peak_kib=$((hfgj_package_kib + hfgj_actual_kib + 8192))
    hfgj_install_need_kib=$(installed_kib "$hfgj_old_package.ipk") || return 1
    check_space "$hfgj_work" "$((hfgj_core_peak_kib + 16384))" || return 1
    # opkg tests raw free space BEFORE removing replacees; no reclaim credit here.
    check_space /usr/libexec "$((hfgj_install_need_kib + 8192))" || return 1
    hfgj_resume_running=0
    if service_running; then hfgj_resume_running=1; fi
    if ! stop_service || ! check_core_peak; then
        start_known_core || echo 'Current verified core could not be restarted' >&2
        return 1
    fi
    if ! rollback_packages; then
        stage rollback-failed || true
        start_known_core || echo 'No registered intact core could be restarted; use saved offline recovery files' >&2
        return 1
    fi
    echo 'Previous core package, actual binary and service state restored.'
}

if [ "$hfgj_action" = --rollback ]; then
    [ $# = 2 ] || { echo 'Supply a backup directory' >&2; exit 1; }
    rollback "$2"
    exit
fi
[ -r "$hfgj_script_dir/rollback-package.sh" ] || { echo 'Recovery helper missing; nothing changed' >&2; exit 1; }
sh "$hfgj_script_dir/rollback-package.sh" --check-tools
for hfgj_command in usign wget jsonfilter readlink df du id uname mv; do
    command -v "$hfgj_command" >/dev/null || { echo "Missing migration tool: $hfgj_command; nothing changed" >&2; exit 1; }
done
installed coreutils-stat || {
    echo 'Missing registered coreutils-stat dependency; run sh install.sh --prepare-tools before migration; nothing changed' >&2; exit 1;
}
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
    printf 'Backup directory: %s; workspace: %s; required mount: %s\n' "$HFGJ_BACKUP_DIR" "$HFGJ_WORK_DIR" "${HFGJ_STORAGE_MOUNT:-not specified}"
    validate_path "$HFGJ_BACKUP_DIR"
    validate_path "$HFGJ_WORK_DIR"
    check_mount
    echo 'Plan only: verify signed candidate and exact rollback IPK; backup; stop Nikki; let opkg replace the package; verify/start. No changes made.'
    exit
fi
if [ "$hfgj_old_package" = mihomo-hfgj ]; then
    echo 'Already using mihomo-hfgj. Update this registered package through LuCI/opkg.'
    exit
fi
[ -r /var/opkg-lists/hfgj-core ] && [ -r /var/opkg-lists/hfgj-core.sig ] || { echo 'Run the HFGJ feed bootstrap first' >&2; exit 1; }
command -v gzip >/dev/null || { echo 'gzip is required to verify opkg indexes' >&2; exit 1; }
hfgj_candidate_index=$(trusted_index /var/opkg-lists/hfgj-core)
hfgj_candidate_version=$(awk 'BEGIN {RS=""; FS="\n"} /(^|\n)Package: mihomo-hfgj(\n|$)/ {for(i=1;i<=NF;i++) if($i~/^Version: /) print substr($i,10)}' "$hfgj_candidate_index")
hfgj_candidate_sha=$(awk 'BEGIN {RS=""; FS="\n"} /(^|\n)Package: mihomo-hfgj(\n|$)/ {for(i=1;i<=NF;i++) if($i~/^SHA256sum: /) print substr($i,12)}' "$hfgj_candidate_index")
rm -f "$hfgj_candidate_index"
[ -n "$hfgj_candidate_version" ] && [ "${#hfgj_candidate_sha}" = 64 ] || { echo 'Invalid signed candidate index' >&2; exit 1; }
[ -r "$hfgj_script_dir/rollback-package.sh" ] || { echo 'Recovery helper missing; service untouched' >&2; exit 1; }
prepare_storage
hfgj_existing_kib=0
if [ -f /usr/libexec/mihomo ]; then hfgj_existing_kib=$(du -k /usr/libexec/mihomo | awk '{print $1}'); fi
hfgj_config_kib=0
if [ -d /etc/nikki ]; then hfgj_config_kib=$(du -sk /etc/nikki | awk '{print $1}'); fi
hfgj_backup_need=$((hfgj_existing_kib * 2 + hfgj_config_kib * 2 + 131072))
hfgj_work_need=$((hfgj_existing_kib * 2 + 131072))
if [ "$(filesystem "$HFGJ_BACKUP_DIR")" = "$(filesystem "$HFGJ_WORK_DIR")" ]; then
    check_space "$HFGJ_BACKUP_DIR" "$((hfgj_backup_need + hfgj_work_need))"
else
    check_space "$HFGJ_BACKUP_DIR" "$hfgj_backup_need"
    check_space "$HFGJ_WORK_DIR" "$hfgj_work_need"
fi
# This reserve is required before stopping; freed deleted executables are not assumed.
check_space /usr/libexec 8192
hfgj_backup=$(mktemp -d "$HFGJ_BACKUP_DIR/migration.XXXXXX")
hfgj_work=$(mktemp -d "$HFGJ_WORK_DIR/migration.XXXXXX")
mkdir -p "$hfgj_work/opkg-tmp"
cd "$hfgj_work"
record_package mihomo-hfgj "$hfgj_candidate_version"
[ "$(sha mihomo-hfgj.ipk)" = "$hfgj_candidate_sha" ] || { echo 'Candidate IPK differs from signed feed index' >&2; exit 1; }
validate_hfgj_package ./mihomo-hfgj.ipk
hfgj_was_running=0
if service_running; then hfgj_was_running=1; fi
hfgj_old_version=
if [ -n "$hfgj_old_package" ]; then
    hfgj_old_version=$(pkg_field "$hfgj_old_package" Version)
    record_package "$hfgj_old_package" "$hfgj_old_version"
    [ "$(ipk_field "$hfgj_old_package.ipk" Architecture)" = "$DISTRIB_ARCH" ] || { echo 'Original IPK architecture mismatch; service untouched' >&2; exit 1; }
    cp /usr/libexec/mihomo "$hfgj_backup/core.binary"
    hfgj_old_sha=$(sha "$hfgj_backup/core.binary")
    hfgj_old_ipk_sha=$(sha "$hfgj_old_package.ipk")
else
    hfgj_old_sha=
    hfgj_old_ipk_sha=
fi
hfgj_install_need_kib=$(installed_kib mihomo-hfgj.ipk)
if [ -n "$hfgj_old_package" ]; then
    hfgj_old_install_kib=$(installed_kib "$hfgj_old_package.ipk")
    if [ "$hfgj_old_install_kib" -gt "$hfgj_install_need_kib" ]; then hfgj_install_need_kib=$hfgj_old_install_kib; fi
    cp "$hfgj_script_dir/rollback-package.sh" "$hfgj_backup/"
    sh "$hfgj_backup/rollback-package.sh" --build "$hfgj_old_package.ipk" "$hfgj_backup/recovery.ipk" "$hfgj_work"
    sh "$hfgj_backup/rollback-package.sh" --verify "$hfgj_old_package.ipk" "$hfgj_backup/recovery.ipk" "$hfgj_work"
    opkg_work compare-versions "$hfgj_old_version~hfgjrestore" '<' "$hfgj_old_version"
    hfgj_recovery_sha=$(sha "$hfgj_backup/recovery.ipk")
    hfgj_helper_sha=$(sha "$hfgj_backup/rollback-package.sh")
else
    hfgj_recovery_sha=
    hfgj_helper_sha=
fi
check_space /usr/libexec "$((hfgj_install_need_kib + 8192))"
hfgj_new_kib=$(bytes_kib candidate)
hfgj_rollback_peak=0
if [ -n "$hfgj_old_package" ]; then
    hfgj_old_package_bytes=$(ipk_payload "$hfgj_old_package.ipk" usr/libexec/mihomo | wc -c)
    [ "$hfgj_old_package_bytes" -gt 0 ] || { echo 'Rollback package has no core payload' >&2; exit 1; }
    hfgj_rollback_peak=$(((hfgj_old_package_bytes + 1023) / 1024 + $(bytes_kib "$hfgj_backup/core.binary")))
fi
hfgj_core_peak_kib=$hfgj_new_kib
if [ "$hfgj_rollback_peak" -gt "$hfgj_core_peak_kib" ]; then hfgj_core_peak_kib=$hfgj_rollback_peak; fi
hfgj_core_peak_kib=$((hfgj_core_peak_kib + 8192))
# Keep verified packages in the backup, and extraction/staging in the workspace.
cp mihomo-hfgj.ipk "$hfgj_backup/"
if [ -n "$hfgj_old_package" ]; then cp "$hfgj_old_package.ipk" "$hfgj_backup/"; fi
cd "$hfgj_backup"
cp "$hfgj_script_dir/migrate.sh" "$hfgj_backup/migrate.sh"
printf 'recovery_protocol=2\nwork_dir=%s\nstorage_mount=%s\n' "$hfgj_work" "$HFGJ_STORAGE_MOUNT" > state
printf 'old_package=%s\nold_version=%s\nwas_running=%s\nold_binary_sha256=%s\nold_ipk_sha256=%s\nrecovery_ipk_sha256=%s\nrecovery_helper_sha256=%s\ncandidate_binary_sha256=%s\n' "$hfgj_old_package" "$hfgj_old_version" "$hfgj_was_running" "$hfgj_old_sha" "$hfgj_old_ipk_sha" "$hfgj_recovery_sha" "$hfgj_helper_sha" "$hfgj_expected_sha" >> state
stage prepared
cp /usr/lib/opkg/status opkg-status.snapshot
if [ -f /etc/config/nikki ]; then cp /etc/config/nikki nikki.uci.snapshot; fi
if [ -d /etc/nikki ]; then tar -czf nikki.private.snapshot.tar.gz -C /etc nikki; fi
check_space /usr/libexec 8192
if [ -n "$hfgj_old_package" ]; then
    unchanged_original || { echo 'Original package/binary changed during preparation; service untouched' >&2; exit 1; }
fi
hfgj_transaction_active=1
hfgj_install_attempted=0
finish_transaction() {
    hfgj_exit=$?
    trap - EXIT HUP INT TERM
    if [ "$hfgj_transaction_active" = 1 ] && [ "$hfgj_exit" != 0 ]; then
        echo "Migration failed; backup: $hfgj_backup" >&2
        if [ "$hfgj_install_attempted" = 0 ]; then
            restore_service || echo 'Could not restore the unchanged core service state' >&2
            stage failed-before-install || true
        elif [ -n "$hfgj_old_package" ]; then
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
stage stopping
stop_service
check_core_peak
hfgj_failed=0
stage installing-candidate
hfgj_install_attempted=1
opkg_work install ./mihomo-hfgj.ipk || hfgj_failed=1
if [ "$hfgj_failed" = 0 ]; then stage verifying-candidate || hfgj_failed=1; fi
if [ "$hfgj_failed" = 0 ]; then verify_installed || hfgj_failed=1; fi
if [ "$hfgj_failed" = 0 ] && [ "$hfgj_was_running" = 1 ]; then
    stage starting-candidate || hfgj_failed=1
    /etc/init.d/nikki start || hfgj_failed=1
    if [ "$hfgj_failed" = 0 ]; then sleep 2; service_running || hfgj_failed=1; fi
fi
if [ "$hfgj_failed" != 0 ]; then
    exit 1
fi
stage completed
hfgj_transaction_active=0
printf 'Migrated core package. Backup: %s\n' "$hfgj_backup"
echo 'Check provider loading and connectivity next; service status alone is not functional validation.'
