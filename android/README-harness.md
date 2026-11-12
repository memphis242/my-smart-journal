# Android end-to-end harness

Instrumented tests run on a headless API 37 emulator against the real backend on deterministic fake model routes.

```sh
export MSJ_ANDROID_BUILD_ROOT=/workspace/build/<your-build-root>   # venv, data, AVD, logs, reports all live here
android/scripts/e2e.sh              # backend + emulator + :app:connectedDebugAndroidTest, then stops both
android/scripts/e2e.sh --keep       # leave them running
android/scripts/e2e.sh --no-grant   # do not grant ACCESS_LOCAL_NETWORK (the tests then time out reaching 10.0.2.2)
```

Pieces: `scripts/env.sh` (settings), `scripts/backend.sh` (setup|start|stop|status|token-file), `scripts/emulator.sh` (create|start|stop|status|wait). Reports go to `<build root>/e2e-results/<timestamp>`.

- The emulator uses `-gpu swangle_indirect`. The older `swiftshader_indirect`, `guest` and `host` modes crash qemu with SIGSEGV on SELinux-enforcing hosts (denied `execheap`). Override with `MSJ_EMULATOR_GPU`, extra flags with `MSJ_EMULATOR_EXTRA_ARGS`.
- On API 37 the app needs the runtime permission `ACCESS_LOCAL_NETWORK` to reach `10.0.2.2`; without it connections time out. Tests get it from `E2eConfig.localNetworkRule()` (a `GrantPermissionRule`, skipped below API 37), which survives the orchestrator's `clearPackageData`.
- The token is pushed to `/data/local/tmp/msj-e2e-token` (mode 600) and read by `E2eConfig.token` through UiAutomation's shell; never log it.
