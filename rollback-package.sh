#!/bin/sh
# Derive a local recovery IPK from a previously authenticated exact original IPK.
# No payload recompression, downloads, package-manager calls or service changes.
set -eu
umask 077
rb_action=${1:-}
rb_check_stat() {
    command -v stat >/dev/null &&
        rb_probe=$(stat -c '%a:%u:%g:%Y' "$0" 2>/dev/null) &&
        printf '%s\n' "$rb_probe" | awk -F: 'NF!=4 {exit 1} {for(i=1;i<=NF;i++) if($i!~/^[0-9]+$/) exit 1} END {if(NR!=1) exit 1}' &&
        rb_short=$(stat -c '%a:%u:%g' "$0" 2>/dev/null) &&
        [ "$rb_short" = "${rb_probe%:*}" ] || {
        echo 'Missing or incompatible stat; needs -c %a:%u:%g:%Y and -c %a:%u:%g. Run sh install.sh --prepare-tools before migration; service untouched.' >&2
        return 1
    }
}
rb_check_tools() {
    for rb_command in tar gzip awk sha256sum find mktemp cat cut sed sort uniq wc cp dirname basename chmod grep rm mkdir; do
        command -v "$rb_command" >/dev/null || { echo "Missing recovery tool: $rb_command; service untouched" >&2; return 1; }
    done
    rb_check_stat
}
case "$rb_action" in
    --check-stat) [ $# = 1 ] || exit 1; rb_check_stat; exit ;;
    --check-tools) [ $# = 1 ] || exit 1; rb_check_tools; exit ;;
    --build|--verify) [ $# = 4 ] || exit 1 ;;
    *) echo 'Usage: rollback-package.sh --check-tools | --check-stat | --build|--verify ORIGINAL.ipk RECOVERY.ipk WORK_DIR' >&2; exit 1 ;;
esac
rb_check_tools
rb_original=$2
rb_recovery=$3
rb_workspace=$4
case "$rb_workspace" in /*) ;; *) echo 'Recovery workspace must be absolute' >&2; exit 1 ;; esac
case "$rb_workspace" in /|*[!a-zA-Z0-9/._-]*|*/../*|*/..) exit 1 ;; esac
[ -d "$rb_workspace" ] || exit 1
rb_original=$(cd "$(dirname "$rb_original")" && printf '%s/%s' "$(pwd -P)" "$(basename "$rb_original")")
rb_recovery=$(cd "$(dirname "$rb_recovery")" && printf '%s/%s' "$(pwd -P)" "$(basename "$rb_recovery")")
[ "$rb_original" != "$rb_recovery" ] || exit 1
rb_stage=$(mktemp -d "$rb_workspace/recovery.XXXXXX")
trap 'rm -rf "$rb_stage"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# macOS bsdtar defaults to PAX, which this router opkg cannot read. GNU/bsdtar
# can explicitly emit USTAR; BusyBox tar emits it natively and has no --format.
rb_format=
if tar --format=ustar -czf "$rb_stage/format-probe.tar.gz" -T /dev/null 2>/dev/null; then
    rb_format=--format=ustar
else
    tar --help 2>&1 | grep -q BusyBox || { echo 'Unsupported tar writer; service untouched' >&2; exit 1; }
fi
rb_sha() { sha256sum "$1" | cut -d ' ' -f 1; }
rb_extract() {
    # Only the gzip/tar layout shipped by the supported OpenWrt SDK is accepted.
    # Caller authenticates the original before this helper is used.
    mkdir -p "$2/control"
    tar -tzf "$1" > "$2/outer-raw" || return 1
    sed 's|^\./||' "$2/outer-raw" | grep -v '^$' | sort > "$2/outer-list"
    printf 'control.tar.gz\ndata.tar.gz\ndebian-binary\n' > "$2/outer-expected"
    [ "$(rb_sha "$2/outer-list")" = "$(rb_sha "$2/outer-expected")" ] || return 1
    for rb_part in debian-binary control.tar.gz data.tar.gz; do
        if ! tar -xzOf "$1" "./$rb_part" > "$2/$rb_part" 2>/dev/null; then
            tar -xzOf "$1" "$rb_part" > "$2/$rb_part" || return 1
        fi
    done
    [ "$(cat "$2/debian-binary")" = 2.0 ] || return 1
    gzip -t "$2/data.tar.gz" || return 1
    tar -tzf "$2/control.tar.gz" > "$2/control-raw" || return 1
    sed 's|^\./||' "$2/control-raw" | grep -v '^$' > "$2/control-list"
    # Official controls are flat regular files. Reject unsupported nesting/traversal.
    while IFS= read -r rb_member_name; do
        case "$rb_member_name" in ''|.|..|*[!a-zA-Z0-9_.+-]*) return 1 ;; esac
    done < "$2/control-list"
    [ "$(sort "$2/control-list" | uniq | wc -l)" = "$(wc -l < "$2/control-list")" ] || return 1
    # -p is necessary: umask 077 must not silently strip original script modes.
    tar -xzpf "$2/control.tar.gz" -C "$2/control" || return 1
    [ -z "$(find "$2/control" ! -type f ! -type d)" ] || return 1
    while IFS= read -r rb_member_name; do
        [ -f "$2/control/$rb_member_name" ] || return 1
    done < "$2/control-list"
    [ -f "$2/control/control" ] || return 1
}
rb_extract "$rb_original" "$rb_stage/original"
rb_control="$rb_stage/original/control/control"
rb_name=$(sed -n 's/^Package: //p' "$rb_control")
rb_version=$(sed -n 's/^Version: //p' "$rb_control")
case "$rb_name" in mihomo-meta|mihomo-alpha|mihomo-hfgj) ;; *) echo 'Unsupported recovery package' >&2; exit 1 ;; esac
case "$rb_version" in ''|*[!a-zA-Z0-9.+:~_-]*|*~hfgjrestore*) echo 'Unsupported original version' >&2; exit 1 ;; esac
rb_replace='mihomo-hfgj-rollback'
if [ "$rb_name" != mihomo-hfgj ]; then rb_replace="mihomo-hfgj, $rb_replace"; fi
awk -v version="$rb_version~hfgjrestore" -v replace="$rb_replace" '
    /^[ \t]/ {if (rewritten) {bad=1; exit 1}}
    /^[^ \t]/ {rewritten=0}
    /^Package: / {if (++package != 1) exit 1}
    /^Version: / {if (++v != 1) exit 1; rewritten=1; print "Version: " version; next}
    /^Conflicts: / {if (++c != 1) exit 1; rewritten=1; conflicts=substr($0,12); next}
    /^Replaces: / {if (++r != 1) exit 1; rewritten=1; replaces=substr($0,11); next}
    {print}
    END {if (bad || package!=1 || v!=1 || c>1 || r>1) exit 1;
         print "Conflicts: " (conflicts ? conflicts ", " : "") replace;
         print "Replaces: " (replaces ? replaces ", " : "") replace}
' "$rb_control" > "$rb_stage/expected-control"
if [ "$rb_action" = --build ]; then
    [ ! -e "$rb_recovery" ] || { echo 'Recovery output already exists' >&2; exit 1; }
    # Copying preserves owners/modes/timestamps of maintainer scripts. Only control changes.
    mkdir "$rb_stage/output"
    cp -p "$rb_stage/original/debian-binary" "$rb_stage/original/data.tar.gz" "$rb_stage/output/"
    cat "$rb_stage/expected-control" > "$rb_control"
    # rb_format is an optional constant option, never user input.
    tar $rb_format -czf "$rb_stage/output/control.tar.gz" -C "$rb_stage/original/control" .
    tar $rb_format -czf "$rb_recovery" -C "$rb_stage/output" ./debian-binary ./control.tar.gz ./data.tar.gz
else
    rb_extract "$rb_recovery" "$rb_stage/derived"
    [ "$(rb_sha "$rb_stage/original/data.tar.gz")" = "$(rb_sha "$rb_stage/derived/data.tar.gz")" ]
    [ "$(rb_sha "$rb_stage/original/debian-binary")" = "$(rb_sha "$rb_stage/derived/debian-binary")" ]
    [ "$(rb_sha "$rb_stage/expected-control")" = "$(rb_sha "$rb_stage/derived/control/control")" ]
    sort "$rb_stage/original/control-list" > "$rb_stage/a"
    sort "$rb_stage/derived/control-list" > "$rb_stage/b"
    [ "$(rb_sha "$rb_stage/a")" = "$(rb_sha "$rb_stage/b")" ]
    while IFS= read -r rb_file; do
        [ "$rb_file" = control ] && continue
        [ "$(rb_sha "$rb_stage/original/control/$rb_file")" = "$(rb_sha "$rb_stage/derived/control/$rb_file")" ]
        rb_original_stat=$(stat -c '%a:%u:%g:%Y' "$rb_stage/original/control/$rb_file")
        rb_derived_stat=$(stat -c '%a:%u:%g:%Y' "$rb_stage/derived/control/$rb_file")
        [ "$rb_original_stat" = "$rb_derived_stat" ]
    done < "$rb_stage/original/control-list"
    rb_original_stat=$(stat -c '%a:%u:%g' "$rb_control")
    rb_derived_stat=$(stat -c '%a:%u:%g' "$rb_stage/derived/control/control")
    [ "$rb_original_stat" = "$rb_derived_stat" ]
fi
printf 'Recovery package %s verified for %s %s\n' "$rb_action" "$rb_name" "$rb_version"
