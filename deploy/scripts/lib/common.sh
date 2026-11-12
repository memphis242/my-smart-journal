# shellcheck shell=bash
#
# Shared helpers for the msj host scripts. bootstrap.sh installs this file as
# /usr/local/lib/msj/common.sh and every msj-* script sources it. It defines
# the host layout in one place, so a path changed here changes everywhere.
#
# Callers set MSJ_JOB (the name used in messages) and, for timer jobs,
# MSJ_UNIT (the systemd unit whose journal holds the full log) before
# sourcing this file, and run under `set -Eeuo pipefail`.

# ---- Layout (docs/deploy/RUNBOOK.md, "The layout on the box") ------------
# Constants used by the scripts that source this file, not by it.
# shellcheck disable=SC2034
readonly MSJ_MOUNT=/srv/msj                  # LUKS volume mount point
readonly MSJ_DATA=/srv/msj/data          # MSJ_DATA_DIR: journal.db, audio/, token.secret
readonly MSJ_SECRETS_DIR=/srv/msj/secrets    # root-only env files
readonly MSJ_BACKUP_DIR=/srv/msj/backup      # local snapshots (nightly, pre-migrate)
readonly MSJ_RESTORE_DIR=/srv/msj/restore    # restore drill and manual restores
readonly MSJ_SWAPFILE=/srv/msj/swapfile
readonly MSJ_HOST_ENV=/etc/msj/host.env      # MSJ_HOSTNAME, MSJ_BIND_IP, MSJ_LUKS_DEVICE
readonly MSJ_HC_ENV=/etc/msj/healthchecks.env
readonly MSJ_RELEASES=/opt/msj/releases
readonly MSJ_CURRENT=/opt/msj/current        # symlink to the live release
readonly MSJ_USER=msj
readonly MSJ_MAPPER=msj-data                 # /dev/mapper/msj-data
readonly MSJ_SERVE_URL=http://127.0.0.1:8765

# restic keeps its cache on the encrypted volume: it holds repository
# metadata, and the boot disk is not encrypted.
export RESTIC_CACHE_DIR=/srv/msj/restic-cache

: "${MSJ_JOB:?set MSJ_JOB before sourcing common.sh}"
msj_fail_reason=""
msj_ok_summary=""
_msj_hc_url=""

msj_log() { printf '%s: %s\n' "$MSJ_JOB" "$*"; }
msj_warn() { printf '%s: warning: %s\n' "$MSJ_JOB" "$*" >&2; }

# Record the reason, print it, and exit non-zero. The reason is also what the
# Healthchecks.io failure ping carries, so write it as what failed, why, and
# what to do next.
msj_die() {
  msj_fail_reason="$*"
  printf '%s: %s\n' "$MSJ_JOB" "$*" >&2
  exit 1
}

# Name the command that failed when `set -e` ends the script, instead of
# leaving only a bare exit status behind.
_msj_on_err() {
  local status=$? line=$1 cmd=$2
  if [[ -z $msj_fail_reason ]]; then
    msj_fail_reason="command failed with status $status at line $line: $cmd"
    printf '%s: %s\n' "$MSJ_JOB" "$msj_fail_reason" >&2
  fi
}
trap '_msj_on_err "$LINENO" "$BASH_COMMAND"' ERR

msj_require_root() {
  [[ $EUID -eq 0 ]] || msj_die "must run as root; reconnect as root over NetBird and rerun $0"
}

# The volume counts as unlocked only when /srv/msj is mounted from the LUKS
# mapping. `mountpoint -q` is not enough: inside a sandboxed unit,
# ReadWritePaths=/srv/msj bind-mounts the bare directory onto itself, which
# makes it a mount point even while the volume is locked.
msj_is_unlocked() {
  [[ $(findmnt -n -o SOURCE --target "$MSJ_MOUNT" 2>/dev/null) == "/dev/mapper/$MSJ_MAPPER" ]] \
    && mountpoint -q "$MSJ_MOUNT"
}

msj_require_unlocked() {
  msj_is_unlocked || msj_die "$MSJ_MOUNT is not mounted, so the encrypted volume is locked and there is no data or secrets to work with. Unlock it with msj-unlock (RUNBOOK 'Unlock after a reboot'), then rerun."
}

# Source KEY=value lines and export every key. A missing or unreadable file
# is an error that names the file.
msj_source_env() {
  local file=$1
  [[ -r $file ]] || msj_die "cannot read $file (missing, or wrong permissions). Create it as described in docs/deploy/RUNBOOK.md section 'Secrets and configuration' (owner root, mode 600)."
  set -a
  # shellcheck source=/dev/null
  . "$file"
  set +a
}

# Run a command as the unprivileged service account, with a clean environment.
# setpriv is used rather than su/runuser: it needs no PAM session and works
# inside a sandboxed systemd unit.
msj_as_service_user() {
  setpriv --reuid="$MSJ_USER" --regid="$MSJ_USER" --init-groups --reset-env -- "$@"
}

# Load restic's configuration and fail early, naming the missing key, rather
# than letting restic fail later with a generic message.
msj_load_restic_env() {
  msj_source_env "$MSJ_SECRETS_DIR/backup.env"
  local key
  for key in RESTIC_REPOSITORY RESTIC_PASSWORD_FILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
    [[ -n ${!key:-} ]] || msj_die "$MSJ_SECRETS_DIR/backup.env does not set $key; add it (see deploy/env/backup.env.example)"
  done
  [[ -r $RESTIC_PASSWORD_FILE ]] || msj_die "RESTIC_PASSWORD_FILE $RESTIC_PASSWORD_FILE is not readable; restore it from the password manager"
  install -d -m 700 "$RESTIC_CACHE_DIR"
}

# Report this run to a Healthchecks.io check: /start now, then /<exit status>
# on exit with a one-line reason as the body. The ping URLs live on the boot
# disk (/etc/msj/healthchecks.env) on purpose, so that a run refused because
# the volume is locked still reports why rather than going silent.
msj_ping_on_exit() {
  local var=$1
  if [[ -r $MSJ_HC_ENV ]]; then
    set -a
    # shellcheck source=/dev/null
    . "$MSJ_HC_ENV"
    set +a
  fi
  _msj_hc_url=${!var:-}
  # MSJ_NO_PING=1 runs a job without reporting, for retries whose
  # intermediate failures should not page anyone.
  [[ ${MSJ_NO_PING:-0} == 1 ]] && _msj_hc_url=""
  if [[ -z $_msj_hc_url ]]; then
    [[ ${MSJ_NO_PING:-0} == 1 ]] \
      || msj_warn "$var is not set in $MSJ_HC_ENV, so this run reports to no dead-man switch"
  else
    curl -fsS -m 10 --retry 2 -o /dev/null "$_msj_hc_url/start" \
      || msj_warn "could not reach the Healthchecks.io check $var (/start); continuing"
  fi
  trap _msj_ping_exit EXIT
}

# Scripts that need cleanup on exit set MSJ_CLEANUP to a function name instead
# of installing their own EXIT trap: a trap that runs cleanup first would
# replace the exit status with cleanup's, turning a failure into a success.
_msj_ping_exit() {
  local status=$? body
  if [[ -n ${MSJ_CLEANUP:-} ]]; then
    "$MSJ_CLEANUP" || msj_warn "cleanup ($MSJ_CLEANUP) failed"
  fi
  if [[ -n $_msj_hc_url ]]; then
    if (( status == 0 )); then
      body=${msj_ok_summary:-ok}
    else
      body="${msj_fail_reason:-exited with status $status}. Full log: journalctl -u ${MSJ_UNIT:-$MSJ_JOB} -n 200"
    fi
    printf '%s\n' "$body" | curl -fsS -m 10 --retry 2 -o /dev/null --data-binary @- \
      "$_msj_hc_url/$status" \
      || msj_warn "could not report exit status $status to Healthchecks.io"
  fi
  exit "$status"
}

# Snapshot the live database consistently with SQLite's online backup API, as
# the service account so that no root-owned -wal or -shm file is ever created
# next to journal.db (the app could not open it afterwards). Never copy
# journal.db with cp: in WAL mode the newest commits live in journal.db-wal.
msj_snapshot_db() {
  local dest=$1 partial check
  partial="$dest.partial"
  [[ -f $MSJ_DATA/journal.db ]] || msj_die "$MSJ_DATA/journal.db does not exist; run msj-release install first, or restore it"
  msj_as_service_user rm -f "$partial"
  msj_as_service_user sqlite3 "$MSJ_DATA/journal.db" ".backup '$partial'"
  check=$(msj_as_service_user sqlite3 "$partial" 'PRAGMA integrity_check;' 2>&1) || true
  if [[ $check != ok ]]; then
    msj_die "PRAGMA integrity_check on the snapshot of journal.db did not return ok (first lines: ${check:0:300}). The live database may be damaged. Nothing was replaced; stop and follow RUNBOOK 'Restore'."
  fi
  msj_as_service_user mv -f "$partial" "$dest"
}

# Run the app's msj command as the service account with the services'
# environment (/etc/msj/msj.env plus /srv/msj/secrets/app.env), in a
# subshell. Leading NAME=value arguments override that environment for this
# run. MSJ_APP_BIN selects the executable (default: the live release).
# Values travel in the environment, never on a command line, where /proc
# would show them to every local user.
msj_app() {
  (
    local bin=${MSJ_APP_BIN:-$MSJ_CURRENT/venv/bin/msj} name
    local keep=" MSJ_CONFIG_FILE MSJ_DATA_DIR MSJ_WEB_DIR HF_HOME HF_HUB_OFFLINE HF_HUB_DISABLE_TELEMETRY PYTHONDONTWRITEBYTECODE PYTHONUNBUFFERED RUNPOD_API_KEY "
    [[ -x $bin ]] || msj_die "$bin is missing; install a release with msj-release install"
    msj_source_env /etc/msj/msj.env
    if [[ -r $MSJ_SECRETS_DIR/app.env ]]; then msj_source_env "$MSJ_SECRETS_DIR/app.env"; fi
    while [[ ${1:-} == *=* ]]; do export "${1?}"; shift; done
    while IFS= read -r name; do
      [[ $keep == *" $name "* ]] || unset "$name" 2>/dev/null || true
    done < <(compgen -e)
    export HOME=/var/lib/msj USER=$MSJ_USER LOGNAME=$MSJ_USER PATH=/usr/bin:/bin
    umask 077
    exec setpriv --reuid="$MSJ_USER" --regid="$MSJ_USER" --init-groups -- "$bin" "$@"
  )
}

# Read one value from the live database, read-only, as the service account.
msj_db_query() {
  msj_as_service_user sqlite3 -readonly "$MSJ_DATA/journal.db" "$1"
}
