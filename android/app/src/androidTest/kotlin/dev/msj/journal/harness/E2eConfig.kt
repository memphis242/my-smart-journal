package dev.msj.journal.harness

import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import org.junit.rules.TestRule
import java.io.BufferedReader
import java.io.InputStreamReader

/**
 * Settings for the end-to-end tests that run against a real local backend (android/scripts/e2e.sh).
 * The server URL arrives as the instrumentation argument `msjServerUrl`; the access token is read
 * from a file the shell user can read and the app cannot, so it never ships in an APK or an argument.
 */
object E2eConfig {
    private const val SERVER_URL_ARG = "msjServerUrl"
    private const val GRANT_LOCAL_NETWORK_ARG = "msjGrantLocalNetwork"
    private const val TOKEN_FILE = "/data/local/tmp/msj-e2e-token"

    /** Runtime permission introduced with API 37 that gates connections to local network addresses. */
    const val LOCAL_NETWORK_PERMISSION = "android.permission.ACCESS_LOCAL_NETWORK"
    private const val LOCAL_NETWORK_MIN_SDK = 37

    /** Base URL of the backend without a trailing slash, e.g. `http://10.0.2.2:18780`. */
    val serverUrl: String by lazy {
        val raw = InstrumentationRegistry.getArguments().getString(SERVER_URL_ARG)
        check(!raw.isNullOrBlank()) {
            "Instrumentation argument $SERVER_URL_ARG is missing. Run through android/scripts/e2e.sh, or pass " +
                "-Pandroid.testInstrumentationRunnerArguments.$SERVER_URL_ARG=http://10.0.2.2:<port> to Gradle."
        }
        raw.trimEnd('/')
    }

    /** The bearer token. Never log or echo it. */
    val token: String by lazy { readToken() }

    /**
     * Grants [LOCAL_NETWORK_PERMISSION] on devices that have it (API 37 and later); a no-op below that,
     * where the permission does not exist. Pass `-Pandroid.testInstrumentationRunnerArguments.msjGrantLocalNetwork=false`
     * to run without the grant (used to observe the ungranted behaviour).
     */
    fun localNetworkRule(): TestRule {
        val grant = InstrumentationRegistry.getArguments().getString(GRANT_LOCAL_NETWORK_ARG) != "false"
        return if (grant && Build.VERSION.SDK_INT >= LOCAL_NETWORK_MIN_SDK) {
            GrantPermissionRule.grant(LOCAL_NETWORK_PERMISSION)
        } else {
            TestRule { base, _ -> base }
        }
    }

    private fun readToken(): String {
        val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
        val descriptor: ParcelFileDescriptor = automation.executeShellCommand("cat $TOKEN_FILE")
        val text =
            ParcelFileDescriptor
                .AutoCloseInputStream(descriptor)
                .use { stream ->
                    BufferedReader(InputStreamReader(stream, Charsets.UTF_8)).readText()
                }.trim()
        check(text.isNotEmpty()) {
            "Token file $TOKEN_FILE is empty or unreadable on the device. " +
                "Push it first: adb -s <serial> push <build root>/backend/token.out $TOKEN_FILE (android/scripts/e2e.sh does this)."
        }
        return text
    }
}
