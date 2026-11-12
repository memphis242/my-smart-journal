#!/usr/bin/env bash
# Usage: e2e.sh [--keep] [--no-grant] [-- <extra Gradle args>]
#   --keep      leave the backend and emulator running afterwards
#   --no-grant  do not grant ACCESS_LOCAL_NETWORK to the tests (observe the ungranted behaviour)
# Brings up the fake-route backend and a headless emulator, runs :app:connectedDebugAndroidTest against
# them, copies the reports to $MSJ_ANDROID_BUILD_ROOT/e2e-results/<timestamp>, then stops what it started.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$here/env.sh"

keep=0
grant=true
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) keep=1; shift ;;
    --no-grant) grant=false; shift ;;
    --) shift; break ;;
    -h|--help) sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) msj_die "unknown argument '$1'. Usage: e2e.sh [--keep] [--no-grant] [-- <extra Gradle args>]" ;;
  esac
done

[ -x "$MSJ_ANDROID_DIR/gradlew" ] || msj_die "$MSJ_ANDROID_DIR/gradlew is missing or not executable."
[ -d "$JAVA_HOME" ] || msj_die "JAVA_HOME=$JAVA_HOME does not exist."

stamp="$(date +%Y%m%d-%H%M%S)"
results="$MSJ_ANDROID_BUILD_ROOT/e2e-results/$stamp"
mkdir -p "$results" "$MSJ_LOG_DIR"
gradle_log="$MSJ_LOG_DIR/gradle-e2e-$stamp.log"
status=1

cleanup() {
  local rc=$?
  set +e
  # Reports and logs are collected whether the run passed or failed.
  if [ -d "$MSJ_ANDROID_BUILD_ROOT/gradle/app" ]; then
    local d
    for d in reports/androidTests outputs/androidTest-results; do
      [ -d "$MSJ_ANDROID_BUILD_ROOT/gradle/app/$d" ] && cp -r "$MSJ_ANDROID_BUILD_ROOT/gradle/app/$d" "$results/" 2>/dev/null
    done
  fi
  cp "$gradle_log" "$MSJ_LOG_DIR"/serve.log "$MSJ_LOG_DIR"/worker.log "$MSJ_LOG_DIR"/emulator.log "$results/" 2>/dev/null
  "$ADB" -s "$MSJ_EMULATOR_SERIAL" logcat -d -t 3000 >"$results/logcat.txt" 2>/dev/null
  "$ADB" -s "$MSJ_EMULATOR_SERIAL" shell rm -f /data/local/tmp/msj-e2e-token >/dev/null 2>&1
  if [ "$keep" = 1 ]; then
    echo "--keep: backend and emulator left running (stop with backend.sh stop / emulator.sh stop)"
  else
    "$here/emulator.sh" stop
    "$here/backend.sh" stop
  fi
  echo "----"
  echo "result : $([ "$status" = 0 ] && echo PASS || echo FAIL)"
  echo "reports: $results"
  echo "logs   : $MSJ_LOG_DIR (gradle: $gradle_log)"
  exit "$rc"
}
trap cleanup EXIT

[ -x "$MSJ_VENV/bin/msj" ] && [ -s "$MSJ_TOKEN_FILE" ] || "$here/backend.sh" setup
"$here/backend.sh" status >/dev/null 2>&1 || { "$here/backend.sh" stop >/dev/null; "$here/backend.sh" start; }

"$here/emulator.sh" create
# A run with --keep leaves the emulator up; the next run reuses it.
"$here/emulator.sh" status >/dev/null 2>&1 || "$here/emulator.sh" start
"$here/emulator.sh" wait

# The token travels as a file (never an argument) that only the shell user can read.
"$ADB" -s "$MSJ_EMULATOR_SERIAL" push "$MSJ_TOKEN_FILE" /data/local/tmp/msj-e2e-token >/dev/null \
  || msj_die "adb push of the token file to $MSJ_EMULATOR_SERIAL failed; is the emulator online? ($MSJ_LOG_DIR/emulator.log)"
"$ADB" -s "$MSJ_EMULATOR_SERIAL" shell chmod 600 /data/local/tmp/msj-e2e-token \
  || msj_die "chmod of /data/local/tmp/msj-e2e-token on $MSJ_EMULATOR_SERIAL failed."

# Gradle's connected tasks must only ever see this emulator.
export ANDROID_SERIAL="$MSJ_EMULATOR_SERIAL"
server_url="http://10.0.2.2:$MSJ_E2E_PORT"
echo "running :app:connectedDebugAndroidTest on $ANDROID_SERIAL against $server_url (log: $gradle_log)"
cd "$MSJ_ANDROID_DIR"
if ./gradlew --console=plain :app:connectedDebugAndroidTest \
    "-Pandroid.testInstrumentationRunnerArguments.msjServerUrl=$server_url" \
    "-Pandroid.testInstrumentationRunnerArguments.msjGrantLocalNetwork=$grant" \
    "$@" >"$gradle_log" 2>&1; then
  status=0
else
  rc=$?
  echo "ERROR: Gradle connected tests failed (exit $rc). Last lines of $gradle_log:" >&2
  tail -n 40 "$gradle_log" >&2
  echo "Next: open the HTML report under $results/androidTests, then check $MSJ_LOG_DIR/serve.log and emulator logcat ($results/logcat.txt)." >&2
  exit "$rc"
fi
