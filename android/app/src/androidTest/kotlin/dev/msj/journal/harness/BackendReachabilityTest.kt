package dev.msj.journal.harness

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TestRule
import org.junit.runner.RunWith
import java.net.HttpURLConnection
import java.net.URL

/** Smoke test of the harness: the emulator reaches the real backend, and the token is accepted. */
@RunWith(AndroidJUnit4::class)
class BackendReachabilityTest {
    @get:Rule
    val localNetwork: TestRule = E2eConfig.localNetworkRule()

    private fun statusOf(
        path: String,
        bearer: String?,
    ): Int {
        val connection = URL(E2eConfig.serverUrl + path).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.requestMethod = "GET"
            if (bearer != null) connection.setRequestProperty("Authorization", "Bearer $bearer")
            return connection.responseCode
        } finally {
            connection.disconnect()
        }
    }

    @Test
    fun healthEndpointAnswers200() {
        assertEquals(200, statusOf("/api/v1/health", null))
    }

    @Test
    fun sessionEndpointAcceptsTheToken() {
        assertEquals(204, statusOf("/api/v1/session", E2eConfig.token))
    }

    private companion object {
        const val TIMEOUT_MS = 10_000
    }
}
