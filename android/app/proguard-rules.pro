# R8 rules for the release build. Libraries (kotlinx.serialization, OkHttp,
# Room, WorkManager) ship their own consumer rules; keep this file minimal.

# Never ship log calls below WARN in release.
-assumenosideeffects class android.util.Log {
    public static int v(...);
    public static int d(...);
    public static int i(...);
}
