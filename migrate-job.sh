#!/bin/sh
# Launch an explicit migration in a new session; keep output and exit status on disk.
set -eu
umask 077
hfgj_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
hfgj_action=${1:-}
case "$hfgj_action" in
    --status)
        [ $# = 2 ] && [ -f "$2/status" ] || { echo 'Supply the printed job directory' >&2; exit 1; }
        cat "$2/status"
        printf 'Log: %s/job.log\n' "$2"
        exit ;;
    --worker)
        [ $# = 2 ] && [ "$hfgj_script_dir" = "$2" ] || exit 1
        hfgj_job=$2
        hfgj_lock=$(cat "$hfgj_job/lock-path")
        finish_job() {
            hfgj_rc=$?
            trap - EXIT HUP INT TERM
            printf 'exit_code=%s\n' "$hfgj_rc" > "$hfgj_job/status.tmp"
            mv "$hfgj_job/status.tmp" "$hfgj_job/status"
            rmdir "$hfgj_lock" || true
            exit "$hfgj_rc"
        }
        hfgj_child=
        cancel_job() {
            hfgj_code=$1
            trap - INT TERM
            if [ -n "$hfgj_child" ]; then
                kill -TERM "$hfgj_child" 2>/dev/null || true
                wait "$hfgj_child" || true
            fi
            exit "$hfgj_code"
        }
        run_phase() {
            sh "$@" &
            hfgj_child=$!
            if wait "$hfgj_child"; then hfgj_phase_rc=0; else hfgj_phase_rc=$?; fi
            hfgj_child=
            return "$hfgj_phase_rc"
        }
        trap finish_job EXIT
        trap 'cancel_job 130' INT
        trap 'cancel_job 143' TERM
        cd "$hfgj_job"
        printf 'state=running\npid=%s\n' "$$" > status
        sh install.sh --check-bootstrap
        run_phase feed.sh
        run_phase migrate.sh --apply
        exit ;;
    --start) [ $# = 1 ] || exit 1 ;;
    *) echo 'Usage: sh migrate-job.sh --start | --status JOB_DIR' >&2; exit 1 ;;
esac
[ "$(id -u)" = 0 ] || { echo 'Run as root' >&2; exit 1; }
command -v nohup >/dev/null
command -v setsid >/dev/null
# The operator must verify this manifest against the reviewed source before launch.
cd "$hfgj_script_dir"
sh install.sh --check-bootstrap
sh migrate.sh --plan
HFGJ_BACKUP_DIR=${HFGJ_BACKUP_DIR:-/root/hfgj-core-backups}
HFGJ_JOB_DIR=${HFGJ_JOB_DIR:-$HFGJ_BACKUP_DIR/jobs}
case "$HFGJ_JOB_DIR" in /*) ;; *) echo 'Job directory must be absolute' >&2; exit 1 ;; esac
case "$HFGJ_JOB_DIR" in /|*[!a-zA-Z0-9/._-]*|*/../*|*/..) exit 1 ;; esac
if [ -n "${HFGJ_STORAGE_MOUNT:-}" ]; then
    case "$HFGJ_JOB_DIR" in "$HFGJ_STORAGE_MOUNT"/*) ;; *) exit 1 ;; esac
fi
mkdir -p "$HFGJ_JOB_DIR"
HFGJ_JOB_DIR=$(cd "$HFGJ_JOB_DIR" && pwd -P)
if [ -n "${HFGJ_STORAGE_MOUNT:-}" ]; then
    case "$HFGJ_JOB_DIR" in "$HFGJ_STORAGE_MOUNT"/*) ;; *) exit 1 ;; esac
fi
case "$(df -Pk "$HFGJ_JOB_DIR" | awk 'END {print $1}')" in tmpfs|ramfs) echo 'Job logs must be persistent' >&2; exit 1 ;; esac
chmod 0700 "$HFGJ_JOB_DIR"
hfgj_lock="$HFGJ_JOB_DIR/active.lock"
mkdir "$hfgj_lock" || { echo 'Another job is active, or an interrupted job lock needs review. Do not rerun blindly.' >&2; exit 1; }
hfgj_launched=0
cleanup_launcher() {
    hfgj_rc=$?
    trap - EXIT
    if [ "$hfgj_launched" = 0 ]; then rmdir "$hfgj_lock" || true; fi
    exit "$hfgj_rc"
}
trap cleanup_launcher EXIT
hfgj_job=$(mktemp -d "$HFGJ_JOB_DIR/job.XXXXXX")
for hfgj_name in feed.sh install.sh migrate.sh migrate-job.sh rollback-package.sh bootstrap.sha256; do
    cp "$hfgj_script_dir/$hfgj_name" "$hfgj_job/"
done
printf '%s\n' "$hfgj_lock" > "$hfgj_job/lock-path"
printf 'state=starting\n' > "$hfgj_job/status"
nohup setsid sh "$hfgj_job/migrate-job.sh" --worker "$hfgj_job" > "$hfgj_job/job.log" 2>&1 < /dev/null &
hfgj_pid=$!
hfgj_launched=1
printf 'Launched job: %s\nLauncher PID: %s\nLog: %s/job.log\nStatus: %s/status\n' "$hfgj_job" "$hfgj_pid" "$hfgj_job" "$hfgj_job"
