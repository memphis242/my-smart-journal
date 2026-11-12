# my-smart-journal for Android

A native thin client for the journal API v1 (`docs/api-v1-contract.md`). It does the phone's jobs: write or record an entry anywhere, keep it safe on the phone until the server has it, browse entries by Today, Week, Month or All, open an entry with its summary, cleaned text and raw transcript, step through entries, run the processing queue, search, and edit with the version check. It runs no models: transcription, cleanup, summaries and extraction stay on the server.

## How it reaches the server

The backend runs on a VPS that is reachable only over the NetBird mesh VPN, at an HTTPS hostname such as `https://journal.<domain>` with a normal Let's Encrypt certificate.

- **NetBird:** the phone needs the NetBird app installed and connected. The app has no VPN code of its own. When NetBird is off, sign-in reports `HOST_NOT_FOUND` or `HOST_UNREACHABLE`, with a hint to connect NetBird. New entries wait in the outbox and upload on their own when the server can be reached again.
- **Trust:** the system CA store is all the app trusts, with no custom CA and no user-installed CAs. Android 17 enables Certificate Transparency for apps targeting API 37, and Let's Encrypt certificates carry the SCTs it needs.
- **HTTPS only:** release builds refuse plain HTTP everywhere. Debug builds allow plain HTTP only to `10.0.2.2` and `127.0.0.1` (the emulator's view of the development machine), through `app/src/debug/res/xml/network_security_config.xml`.
- **Android 17 local-network permission:** apps targeting API 37 need the runtime permission `ACCESS_LOCAL_NETWORK` (Settings: Nearby devices) to reach local-network addresses. Android does not document which ranges count. A NetBird peer has a `100.64.0.0/10` address, so the app checks the server's address at sign-in. If the address is private (RFC 1918, `100.64.0.0/10`, link-local or IPv6 ULA), the app asks for the permission with a reason. If the permission is missing later, the list page shows a notice with an Allow button.

## Toolchain

| Part | Version | Notes |
|---|---|---|
| JDK | 25 (`/usr/lib/jvm/java-25-openjdk`) | Runs Gradle, compiles to Java 17 bytecode |
| Gradle | 9.8.2 (wrapper, SHA-256 pinned) | AGP 9.5 needs 9.6 or newer |
| Android Gradle Plugin | 9.5.1 | Built-in Kotlin, max API level 37 |
| Kotlin | 2.4.21, KSP 2.3.13 | |
| Android SDK | `/workspace/Android/Sdk` | Platform 37, build-tools 37.0.0, NDK 27.2 (only to strip a prebuilt library) |
| compileSdk, targetSdk / minSdk | 37 / 28 | AGP 9.5 supports API 37, so the app targets Android 17 |
| UI | Jetpack Compose (BOM 2026.09.00), Material 3 | Custom light and dark themes |
| Data | OkHttp 5.5.1, kotlinx.serialization 1.13.0, Room 2.8.5, WorkManager 2.12.0 | |
| Fonts | IBM Plex Sans, IBM Plex Mono, Source Serif 4 | SIL OFL 1.1, from google/fonts at commit `c30b221b`; the license texts ship in `app/src/main/assets/licenses/` |

All versions are pinned in `gradle/libs.versions.toml`.

The generated files are kept out of the source tree:

- The build output goes to `$MSJ_ANDROID_BUILD_ROOT`. When that is unset, it goes to `/workspace/build/my-smart-journal-feat-android-app` if `/workspace/build` exists, and otherwise to the gitignored `build/` directories.
- Logs, the emulator AVD, the backend venv, backend data, test reports and the release keystore go under that same build root.

## Layout

```
core/   plain Kotlin/JVM: API client and error model, NDJSON reader, server URL rules,
        date ranges, queue logic, edit-conflict logic, outbox state machine and reconciler
app/    Android: session (Keystore token vault), Room outbox and WorkManager uploader,
        voice recorder, Compose UI (theme, screens), instrumented tests
scripts/  check.sh (quality gate), e2e.sh with backend.sh and emulator.sh (journeys on
          an emulator), make-release-keystore.sh
```

## Build

```sh
cd android
export JAVA_HOME=/usr/lib/jvm/java-25-openjdk ANDROID_HOME=/workspace/Android/Sdk
./gradlew :app:assembleDebug          # <build root>/gradle/app/outputs/apk/debug/app-debug.apk
```

Release build: R8 shrinking and obfuscation, resource shrinking, not debuggable, no cleartext, and Kotlin warnings treated as errors. The signing key is a local key kept in the build root, never in the repository:

```sh
eval "$(scripts/make-release-keystore.sh --print-env)"   # creates <build root>/signing/ once; keep a backup
./gradlew :app:assembleRelease                           # <build root>/gradle/app/outputs/apk/release/app-release.apk
```

The debug build installs as `dev.msj.journal.debug`, next to the release build `dev.msj.journal`. Each build has its own sign-in and its own outbox.

## Quality gates

```sh
scripts/check.sh
```

`check.sh` runs these checks in one parallel Gradle invocation and prints the log path:

- ktlint (`ktlint_official` plus the Compose rules);
- the JVM tests:
  - `core`: the API client against MockWebServer for every error path, the NDJSON reader, date ranges, queue logic, draft reapply, the outbox state machine and the reconciler;
  - `app` (Robolectric): the Room outbox, the uploader and scheduler, capture fields, formatting and entry navigation;
- Android lint on the debug and release variants, with `checkAllWarnings` and `warningsAsErrors`; the three disabled checks are in `app/lint.xml`, each with its reason;
- a strict release build with Kotlin `allWarningsAsErrors`;
- compiling the instrumented-test APK.

Debug builds keep Kotlin warnings as warnings. Kotlin `assert()` is compiled with `-Xassertions=jvm`, so assertions run in debug builds and tests and cost nothing in release, where R8 removes them.

## Journeys on an emulator against the real backend

`scripts/e2e.sh` does the whole run, then stops the backend and emulator on exit:

1. starts the real backend with fake model routes on the development machine;
2. boots a headless API 37 emulator;
3. pushes the access token to the device;
4. runs the instrumented tests (`connectedDebugAndroidTest`).

```sh
cd android
scripts/e2e.sh            # add --keep to leave the backend and emulator running
```

These are the parts it uses:

- **`scripts/backend.sh setup|start|stop|status`:**
  - a Python 3.14 venv under the build root with `pip install -e backend`;
  - `MSJ_CONFIG_FILE=web/e2e-real/fake-backend.toml` (every model task on the deterministic fake provider) and `MSJ_DATA_DIR` under the build root;
  - `msj migrate`, `msj create-token` (written owner-only, never printed);
  - `msj serve --host 127.0.0.1 --port 18780` and `msj worker`.
- **`scripts/emulator.sh create|start|stop|status|wait`:** AVD `msj-api37` from `system-images;android-37.0;google_apis_playstore;x86_64`. The AVD and the emulator's user data live in the build root. The emulator runs headless (`-no-window`) with KVM, and the script disables animations once it has booted.
- **Ports:** `MSJ_E2E_PORT` (default 18780) and `MSJ_EMULATOR_PORT` (default 5584, serial `emulator-5584`). Gradle is limited to that one emulator through `ANDROID_SERIAL`.
- **Reaching the backend:** the emulator reaches the host at `http://10.0.2.2:18780`. The tests get that URL as the instrumentation argument `msjServerUrl`, and read the token from `/data/local/tmp/msj-e2e-token` through the shell user.

The instrumented tests (`app/src/androidTest`) find nodes by test tags and read data through custom semantics keys. They never assert on visible text. Each test starts from a cleared app (AndroidX Test Orchestrator with `clearPackageData`). The tests:

- `SignInTest`:
  - sign in through Test connection and Sign in;
  - a rejected token (`UNAUTHORIZED`);
  - a closed port (`HOST_UNREACHABLE`);
  - another service (`NOT_A_JOURNAL_SERVER`);
  - plain HTTP to a non-debug host (`INVALID_SERVER_URL`).
- `JourneyTest`:
  - a text entry written in the composer goes through the outbox and appears in the list; opening it, then stepping to the older entry (it must be the server's `neighbors` answer) and swiping back;
  - a voice entry recorded with the recorder screen uploads as Ogg/Opus;
  - a bundled Opus clip uploads through the outbox;
  - Run now drains the queue, and the counts change;
  - search finds an entry and opens it;
  - the edit conflict (`ENTRY_CHANGED`), with reload and reapply;
  - a rejected upload is listed with its code and can be discarded;
  - sign-out wipes the session.
- `TokenVaultTest`: the Keystore round trip, wipe, and a lost key.

## Install on the phone (Samsung Galaxy S26 Ultra, One UI 9 / Android 17)

On the phone, enable Developer options (tap Settings > About phone > Software information > Build number seven times). In Developer options, turn on USB debugging, and optionally Wireless debugging. ADB is not on `PATH` here, so use the SDK's:

```sh
ADB=/workspace/Android/Sdk/platform-tools/adb
```

Over USB:

```sh
"$ADB" devices -l                                  # authorize the computer on the phone when asked
"$ADB" -s SERIAL install -r <build root>/gradle/app/outputs/apk/release/app-release.apk
```

Wirelessly (same Wi-Fi). In Developer options > Wireless debugging, tap "Pair device with pairing code"; the phone then shows an address and a code to use with `pair`. The main Wireless debugging screen shows the separate address to use with `connect`:

```sh
"$ADB" pair PHONE_IP:PAIR_PORT                     # enter the six-digit code
"$ADB" connect PHONE_IP:CONNECT_PORT
"$ADB" -s PHONE_IP:CONNECT_PORT install -r <path to apk>
```

Keep using one signing key for each package. `install -r` of an APK signed with a different key fails. Uninstalling to get past that error deletes the app's data, including its unsent entries.

First run:

1. Connect NetBird.
2. Open Journal, enter the server address and the token from `msj create-token`, and tap Test connection, then Sign in.
3. If the server's address is private, allow local network access when Android asks (Nearby devices).

The launcher icon has Record and Write shortcuts (long-press it). Other apps can share text into a new entry through the share sheet ("Journal entry").

## Verified on the emulator, and what needs the phone

This section summarizes the latest verification run; see the final report for the exact commands and results.

Verified on the API 37 emulator against the real backend: every journey listed above.

Needs the physical phone:

- **NetBird:** the connection over NetBird, and whether Android 17 treats a NetBird peer address (`100.64.0.0/10` over the VPN interface) as local network. The app asks for the permission whenever the server's address is private, so it works either way. If Android does not gate VPN peers, the request is unnecessary but harmless. The device run settles which case applies.
- **HTTPS:** HTTPS with the real Let's Encrypt certificate, including Certificate Transparency, which is on by default for API 37. The emulator tests use plain HTTP to `10.0.2.2`.
- **Microphone:** a real recording's audio quality and the transcription it produces. On the emulator the microphone input is synthetic.
- **StrongBox:** the S26 Ultra has StrongBox, the emulator has not, so the emulator tested the Keystore fallback path.
- **One UI:** background behavior under One UI's battery management (WorkManager uploads after the app is swiped away or after a reboot), and the launcher shortcuts and share sheet.
- **Recording:** pausing a recording when the app goes to the background, which is the platform rule for the microphone in the background.

## Known gaps

- **Duplicate uploads (API gap):** `POST /entries` has no idempotency key. The client prevents duplicates itself:
  - one outbox record per capture, keyed by a client UUID, so a second tap of Save adds nothing;
  - one uploader at a time;
  - before resending, it reconciles any upload whose outcome is unknown against the server's entries for that date (text equal, or audio SHA-256 equal).

  A server-side `Idempotency-Key` would make this exact.
- **Edited entries:** an entry edited on another device before the phone reconciles a lost response no longer shows its first-revision artifact, and could be uploaded twice. See `Reconciler`.
- **Process death while recording:** it loses the recording in progress. A stopped recording that has not been saved yet survives.
