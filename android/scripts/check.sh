#!/usr/bin/env bash
# The local quality gate (everything except the emulator journeys, which are
# scripts/e2e.sh): ktlint with the Compose rules, JVM tests (core and app on
# Robolectric), Android lint (debug and release, warnings as errors), a strict
# release build (Kotlin warnings as errors, R8) and the instrumented-test APK.
set -euo pipefail

usage() { echo "usage: $0" >&2; }
[[ $# -eq 0 ]] || { usage; exit 2; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(dirname "$here")"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-25-openjdk}"
export ANDROID_HOME="${ANDROID_HOME:-/workspace/Android/Sdk}"
build_root="${MSJ_ANDROID_BUILD_ROOT:-/workspace/build/my-smart-journal-feat-android-app}"
mkdir -p "$build_root/logs"
log="$build_root/logs/check-$(date +%Y%m%d-%H%M%S).log"

if [[ -z "${MSJ_RELEASE_SIGNING:-}" && -f "$build_root/signing/release.properties" ]]; then
  export MSJ_RELEASE_SIGNING="$build_root/signing/release.properties"
fi

cd "$project"
echo "Running the gate; full log: $log" >&2
if ! ./gradlew --parallel -Pmsj.strict=true \
    ktlintCheck :core:test :app:testDebugUnitTest :app:lintDebug :app:lintRelease \
    :app:assembleDebug :app:assembleRelease :app:assembleDebugAndroidTest >"$log" 2>&1; then
  echo "error: the gate failed. First problems:" >&2
  grep -E "^(e|w): |FAILED|Error:|\.kt:[0-9]+:[0-9]+|expected:|AssertionError" "$log" | head -40 >&2 || true
  echo "Full log: $log" >&2
  exit 1
fi
echo "Gate passed. APKs:" >&2
find "$build_root/gradle/app/outputs/apk" -name '*.apk' -printf '  %p (%s bytes)\n' >&2
