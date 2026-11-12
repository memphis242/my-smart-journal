# Shared settings for the Android end-to-end harness. Source it, do not execute it.
# Usage: source android/scripts/env.sh
# Every value can be overridden by exporting it first.

# Every value below keeps an exported override, so sourcing again (each script does) is idempotent.

_msj_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MSJ_REPO_ROOT="$(cd "$_msj_here/../.." && pwd)"
export MSJ_ANDROID_DIR="$MSJ_REPO_ROOT/android"
unset _msj_here

export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-25-openjdk}"
export ANDROID_HOME="${ANDROID_HOME:-/workspace/Android/Sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ADB="$ANDROID_HOME/platform-tools/adb"
export EMULATOR_BIN="$ANDROID_HOME/emulator/emulator"
export AVDMANAGER="$ANDROID_HOME/cmdline-tools/latest/bin/avdmanager"

export MSJ_ANDROID_BUILD_ROOT="${MSJ_ANDROID_BUILD_ROOT:-/workspace/build/my-smart-journal-feat-android-app}"
export MSJ_E2E_PORT="${MSJ_E2E_PORT:-18780}"
export MSJ_EMULATOR_PORT="${MSJ_EMULATOR_PORT:-5584}"
export MSJ_EMULATOR_SERIAL="emulator-$MSJ_EMULATOR_PORT"
export MSJ_AVD_NAME="${MSJ_AVD_NAME:-msj-api37}"
export MSJ_SYSTEM_IMAGE="${MSJ_SYSTEM_IMAGE:-system-images;android-37.0;google_apis_playstore;x86_64}"

export MSJ_BACKEND_DIR="$MSJ_ANDROID_BUILD_ROOT/backend"
export MSJ_VENV="$MSJ_BACKEND_DIR/venv"
export MSJ_DATA_DIR="$MSJ_BACKEND_DIR/data"
export MSJ_TOKEN_FILE="$MSJ_BACKEND_DIR/token.out"
export MSJ_LOG_DIR="$MSJ_ANDROID_BUILD_ROOT/logs"
export MSJ_PID_DIR="$MSJ_ANDROID_BUILD_ROOT/pids"
export MSJ_CONFIG_FILE="$MSJ_REPO_ROOT/web/e2e-real/fake-backend.toml"

# Keep emulator and AVD state out of ~/.android.
export ANDROID_AVD_HOME="$MSJ_ANDROID_BUILD_ROOT/avd"
export ANDROID_EMULATOR_HOME="$MSJ_ANDROID_BUILD_ROOT/emulator-home"
export ANDROID_USER_HOME="$MSJ_ANDROID_BUILD_ROOT/android-user"

# Ports owned by other instances; the harness refuses them.
msj_die() { echo "ERROR: $*" >&2; exit 1; }
case "$MSJ_E2E_PORT" in 18765|18770) msj_die "MSJ_E2E_PORT=$MSJ_E2E_PORT belongs to another instance; choose another port.";; esac

