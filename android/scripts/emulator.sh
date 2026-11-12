#!/usr/bin/env bash
# Usage: emulator.sh create | start | stop | status | wait
# Headless API 37 emulator on serial emulator-$MSJ_EMULATOR_PORT for the Android e2e tests.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

SERIAL="$MSJ_EMULATOR_SERIAL"
PID_FILE="$MSJ_PID_DIR/emulator.pid"
LOG="$MSJ_LOG_DIR/emulator.log"
BOOT_TIMEOUT_S="${MSJ_BOOT_TIMEOUT_S:-300}"

usage() { sed -n '2p' "${BASH_SOURCE[0]}" | sed 's/^# //' >&2; exit 2; }
adb_s() { "$ADB" -s "$SERIAL" "$@"; }

check_tools() {
  local t
  for t in "$ADB" "$EMULATOR_BIN" "$AVDMANAGER"; do
    [ -x "$t" ] || msj_die "$t is missing; install the Android SDK component or set ANDROID_HOME (now $ANDROID_HOME)."
  done
  [ -r /dev/kvm ] && [ -w /dev/kvm ] || msj_die "/dev/kvm is not accessible; the x86_64 emulator needs KVM (add your user to the kvm group)."
}

emulator_alive() {
  local pid
  [ -f "$PID_FILE" ] || return 1
  pid="$(cat "$PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

device_listed() { "$ADB" devices | awk -v s="$SERIAL" '$1 == s {found=1} END {exit !found}'; }

cmd_create() {
  check_tools
  mkdir -p "$ANDROID_AVD_HOME" "$ANDROID_EMULATOR_HOME" "$ANDROID_USER_HOME" "$MSJ_LOG_DIR"
  local image_dir="$ANDROID_HOME/$(echo "${MSJ_SYSTEM_IMAGE//;//}")"
  [ -d "$image_dir" ] || msj_die "system image $MSJ_SYSTEM_IMAGE not found at $image_dir; install it with sdkmanager \"$MSJ_SYSTEM_IMAGE\"."
  if [ -f "$ANDROID_AVD_HOME/$MSJ_AVD_NAME.ini" ]; then
    echo "AVD $MSJ_AVD_NAME already exists in $ANDROID_AVD_HOME"
    return 0
  fi
  echo no | "$AVDMANAGER" create avd --name "$MSJ_AVD_NAME" --package "$MSJ_SYSTEM_IMAGE" --device pixel_6 --force \
    >"$MSJ_LOG_DIR/avd-create.log" 2>&1 \
    || msj_die "avdmanager create avd failed; see $MSJ_LOG_DIR/avd-create.log"
  local cfg="$ANDROID_AVD_HOME/$MSJ_AVD_NAME.avd/config.ini"
  [ -f "$cfg" ] || msj_die "avdmanager did not write $cfg; see $MSJ_LOG_DIR/avd-create.log"
  # Set or append each key so reruns are stable.
  local kv key
  for kv in "hw.ramSize=3072" "disk.dataPartition.size=4G" "hw.sdCard=no" "hw.keyboard=yes" "hw.audioInput=yes" "hw.audioOutput=yes"; do
    key="${kv%%=*}"
    if grep -q "^$key=" "$cfg"; then sed -i "s|^$key=.*|$kv|" "$cfg"; else echo "$kv" >>"$cfg"; fi
  done
  echo "created AVD $MSJ_AVD_NAME in $ANDROID_AVD_HOME"
}

cmd_start() {
  check_tools
  [ -f "$ANDROID_AVD_HOME/$MSJ_AVD_NAME.ini" ] || msj_die "AVD $MSJ_AVD_NAME missing; run: $0 create"
  if emulator_alive || device_listed; then
    msj_die "emulator $SERIAL is already running (pid file $PID_FILE); stop it with '$0 stop' or use another MSJ_EMULATOR_PORT."
  fi
  mkdir -p "$MSJ_LOG_DIR" "$MSJ_PID_DIR"
  # swangle_indirect (ANGLE on SwiftShader Vulkan) is the default GPU mode: on SELinux-enforcing hosts
  # (execheap denied) the older swiftshader_indirect/guest/host modes crash qemu with SIGSEGV when the
  # renderer executes JIT code. Override with MSJ_EMULATOR_GPU, e.g. angle_indirect for the host GPU.
  # Audio stays enabled (no -no-audio) so the microphone input of a recorder test works headless.
  # MSJ_EMULATOR_EXTRA_ARGS (word-split on purpose) appends further emulator flags.
  # shellcheck disable=SC2086
  setsid nohup "$EMULATOR_BIN" -avd "$MSJ_AVD_NAME" -port "$MSJ_EMULATOR_PORT" \
    -no-window -no-boot-anim -no-snapshot -gpu ${MSJ_EMULATOR_GPU:-swangle_indirect} -no-metrics ${MSJ_EMULATOR_EXTRA_ARGS:-} \
    </dev/null >"$LOG" 2>&1 &
  echo $! >"$PID_FILE"
  echo "emulator started (pid $(cat "$PID_FILE")), log $LOG"
}

cmd_wait() {
  emulator_alive || msj_die "emulator process is not running; run '$0 start' first. Log: $LOG"
  local t0=$SECONDS v
  "$ADB" start-server >/dev/null 2>&1 || msj_die "adb start-server failed"
  adb_s wait-for-device >/dev/null 2>&1 &
  local waiter=$!
  while kill -0 "$waiter" 2>/dev/null; do
    emulator_alive || msj_die "emulator exited while waiting for $SERIAL; see $LOG"
    (( SECONDS - t0 < BOOT_TIMEOUT_S )) || { kill "$waiter" 2>/dev/null || true; msj_die "$SERIAL not visible to adb after ${BOOT_TIMEOUT_S}s; see $LOG"; }
    sleep 1
  done
  wait "$waiter" || msj_die "adb wait-for-device failed for $SERIAL"
  while true; do
    v="$(adb_s shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
    [ "$v" = 1 ] && break
    emulator_alive || msj_die "emulator exited during boot; see $LOG"
    (( SECONDS - t0 < BOOT_TIMEOUT_S )) || msj_die "sys.boot_completed not set after ${BOOT_TIMEOUT_S}s on $SERIAL; see $LOG"
    sleep 2
  done
  adb_s shell settings put global window_animation_scale 0 >/dev/null
  adb_s shell settings put global transition_animation_scale 0 >/dev/null
  adb_s shell settings put global animator_duration_scale 0 >/dev/null
  adb_s shell svc power stayon true >/dev/null
  adb_s shell input keyevent 224 >/dev/null   # wake
  adb_s shell wm dismiss-keyguard >/dev/null
  adb_s shell input keyevent 82 >/dev/null    # menu: dismisses a swipe lock screen
  adb_s shell settings put secure show_ime_with_hard_keyboard 0 >/dev/null
  echo "$SERIAL booted and ready in $((SECONDS - t0)) s (since wait began)"
}

cmd_stop() {
  if ! emulator_alive && ! device_listed; then
    echo "emulator: not running"; rm -f "$PID_FILE"; return 0
  fi
  if device_listed; then
    adb_s emu kill >/dev/null 2>&1 || echo "adb emu kill failed for $SERIAL; falling back to the recorded pid" >&2
  fi
  local i pid=""
  [ -f "$PID_FILE" ] && pid="$(cat "$PID_FILE")"
  for i in $(seq 1 60); do
    if [[ "$pid" =~ ^[0-9]+$ ]]; then kill -0 "$pid" 2>/dev/null || break; else device_listed || break; fi
    sleep 1
  done
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    echo "emulator pid $pid still alive after 60 s; sending SIGTERM" >&2
    kill "$pid"; sleep 3
  fi
  for i in $(seq 1 30); do device_listed || break; sleep 1; done
  device_listed && msj_die "adb still lists $SERIAL after the emulator stopped; run: $ADB kill-server"
  rm -f "$PID_FILE"
  echo "emulator: stopped"
}

cmd_status() {
  local rc=0
  if emulator_alive; then echo "process: running (pid $(cat "$PID_FILE"))"; else echo "process: not running"; rc=1; fi
  if device_listed; then
    echo "adb: $SERIAL listed; boot_completed=$(adb_s shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
  else
    echo "adb: $SERIAL not listed"; rc=1
  fi
  return $rc
}

case "${1:-}" in
  create) cmd_create ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  wait) cmd_wait ;;
  *) usage ;;
esac
