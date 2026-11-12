#!/usr/bin/env bash
# Creates a local release signing key for side-loading this private app, in the
# build root (never in the repository), and the properties file the release
# build reads through MSJ_RELEASE_SIGNING. Re-running keeps an existing key:
# changing the key means uninstalling the app (and its unsent entries) first.
set -euo pipefail

usage() { echo "usage: $0 [--print-env]" >&2; }
[[ $# -le 1 ]] || { usage; exit 2; }

BUILD_ROOT="${MSJ_ANDROID_BUILD_ROOT:-/workspace/build/my-smart-journal-feat-android-app}"
JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-25-openjdk}"
KEYTOOL="$JAVA_HOME/bin/keytool"
DIR="$BUILD_ROOT/signing"
STORE="$DIR/release.jks"
PROPS="$DIR/release.properties"

if [[ ! -x "$KEYTOOL" ]]; then
  echo "error: keytool not found at $KEYTOOL; set JAVA_HOME to a full JDK" >&2
  exit 1
fi

if [[ ! -f "$STORE" ]]; then
  mkdir -p "$DIR"
  chmod 700 "$DIR"
  password="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"
  (umask 077; "$KEYTOOL" -genkeypair -keystore "$STORE" -storetype PKCS12 -alias journal \
      -keyalg EC -groupname secp256r1 -sigalg SHA256withECDSA -validity 10000 \
      -dname "CN=my-smart-journal local release" \
      -storepass "$password" -keypass "$password" >/dev/null)
  (umask 077; printf 'storeFile=%s\nstorePassword=%s\nkeyAlias=journal\nkeyPassword=%s\n' \
      "$STORE" "$password" "$password" > "$PROPS")
  echo "Created $STORE and $PROPS (owner-only). Back them up: an update must be signed with the same key." >&2
else
  echo "Keeping the existing key $STORE." >&2
fi

if [[ "${1:-}" == "--print-env" ]]; then
  echo "export MSJ_RELEASE_SIGNING=$PROPS"
fi
