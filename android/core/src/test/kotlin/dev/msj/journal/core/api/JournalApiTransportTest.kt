package dev.msj.journal.core.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.SocketEffect
import okhttp3.ConnectionSpec
import okhttp3.Dns
import okhttp3.OkHttpClient
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Test
import java.io.File
import java.io.IOException
import java.net.InetAddress
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Transport failures. Delivery is the contract under test: NOT_SENT only when
 * no request byte was written, MAYBE_SENT afterwards.
 */
internal class JournalApiTransportTest {
    private val servers = mutableListOf<MockWebServer>()

    @After
    fun stop() {
        servers.forEach { it.close() }
    }

    private fun startServer(configure: MockWebServer.() -> Unit = {}): MockWebServer =
        MockWebServer().also {
            it.configure()
            it.start(InetAddress.getByName("127.0.0.1"), 0)
            servers += it
        }

    private fun client(
        server: MockWebServer,
        http: OkHttpClient = testClient(),
    ) = JournalApiClient(serverUrlFor(server), TEST_TOKEN, http)

    private fun parse(
        text: String,
        cleartext: Set<String> = emptySet(),
    ): ServerUrl = (ServerUrl.parse(text, cleartext) as ServerUrlResult.Valid).url

    private fun textEntry() = NewTextEntry("Hello", null, emptyList(), "2026-11-11")

    // ------------------------------------------------------------------ before the request starts

    @Test
    fun `an unresolvable host is HOST_NOT_FOUND and not sent`() {
        val dns = Dns { host -> throw UnknownHostException("no address for $host") }
        val api =
            JournalApiClient(
                parse("https://journal.example.com"),
                TEST_TOKEN,
                testClient().newBuilder().dns(dns).build(),
            )
        for (call in listOf<suspend () -> Unit>({ api.health() }, { api.createTextEntry(textEntry()) })) {
            val error = failure(call)
            error.assertCode("HOST_NOT_FOUND", ErrorKind.NETWORK, Delivery.NOT_SENT)
            assertTrue(error.hint.orEmpty().contains("NetBird"), error.hint)
            assertTrue(error.hint.orEmpty().contains("connect"), error.hint)
            assertTrue(error.message.contains("journal.example.com"), error.message)
            assertTrue(error.detail("Cause").orEmpty().contains("UnknownHostException"))
            assertEquals("journal.example.com", error.detail("Host"))
        }
    }

    @Test
    fun `a refused connection is HOST_UNREACHABLE and not sent`() {
        val closed = startServer().also { it.close() }
        val api =
            JournalApiClient(parse("http://127.0.0.1:${closed.port}", setOf("127.0.0.1")), TEST_TOKEN, testClient())
        for (call in listOf<suspend () -> Unit>(
            { api.health() },
            { api.createTextEntry(textEntry()) },
            { api.runBatch() },
        )) {
            val error = failure(call)
            error.assertCode("HOST_UNREACHABLE", ErrorKind.NETWORK, Delivery.NOT_SENT)
            assertTrue(error.hint.orEmpty().contains("NetBird"))
            assertTrue(error.hint.orEmpty().contains("running"))
            assertTrue(error.detail("Cause").orEmpty().contains("ConnectException"), error.detail("Cause"))
        }
    }

    @Test
    fun `a failure outside the connection before any write is NETWORK_ERROR and not sent`() {
        val server = startServer()
        val http =
            testClient()
                .newBuilder()
                .addInterceptor {
                    throw IOException("interceptor failed while sending Bearer $TEST_TOKEN")
                }.build()
        val error = failure { client(server, http).createTextEntry(textEntry()) }
        error.assertCode("NETWORK_ERROR", ErrorKind.NETWORK, Delivery.NOT_SENT)
        assertTrue(error.detail("Cause").orEmpty().contains("[token]"), error.detail("Cause"))
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `a client that forbids cleartext reports CLEARTEXT_NOT_PERMITTED and not sent`() {
        val server = startServer()
        val http = testClient().newBuilder().connectionSpecs(listOf(ConnectionSpec.MODERN_TLS)).build()
        val error = failure { client(server, http).health() }
        error.assertCode("CLEARTEXT_NOT_PERMITTED", ErrorKind.NETWORK, Delivery.NOT_SENT)
        assertTrue(error.hint.orEmpty().contains("https://"))
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `a missing recording file is not sent`() {
        val server = startServer()
        val missing = File("/nonexistent/recording.ogg")
        val error =
            failure {
                client(
                    server,
                ).createAudioEntry(NewAudioEntry(missing, "audio/ogg", "r.ogg", null, emptyList(), "2026-11-11"))
            }
        error.assertCode("NETWORK_ERROR", ErrorKind.NETWORK, Delivery.NOT_SENT)
        assertTrue(error.message.contains("missing or unreadable"))
        assertEquals(0, server.requestCount)
    }

    // ------------------------------------------------------------------ TLS

    private fun tlsServer(hostnames: List<String>): Pair<MockWebServer, HeldCertificate> {
        val held =
            HeldCertificate
                .Builder()
                .apply { hostnames.forEach { addSubjectAlternativeName(it) } }
                .build()
        val certs = HandshakeCertificates.Builder().heldCertificate(held).build()
        val server = startServer { useHttps(certs.sslSocketFactory()) }
        return server to held
    }

    @Test
    fun `an untrusted certificate is TLS_FAILED and not sent`() {
        val (server, _) = tlsServer(listOf("127.0.0.1"))
        val api = JournalApiClient(parse("https://127.0.0.1:${server.port}"), TEST_TOKEN, testClient())
        for (call in listOf<suspend () -> Unit>({ api.health() }, { api.createTextEntry(textEntry()) })) {
            val error = failure(call)
            error.assertCode("TLS_FAILED", ErrorKind.NETWORK, Delivery.NOT_SENT)
            assertTrue(error.hint.orEmpty().contains("publicly trusted certificate"), error.hint)
            assertTrue(error.hint.orEmpty().contains("date and time"), error.hint)
            assertTrue(error.detail("Cause").orEmpty().isNotBlank())
        }
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `a hostname mismatch is TLS_FAILED and not sent`() {
        val (server, held) = tlsServer(listOf("other.example"))
        val trust = HandshakeCertificates.Builder().addTrustedCertificate(held.certificate).build()
        val http = testClient().newBuilder().sslSocketFactory(trust.sslSocketFactory(), trust.trustManager).build()
        val error = failure { JournalApiClient(parse("https://127.0.0.1:${server.port}"), TEST_TOKEN, http).health() }
        error.assertCode("TLS_FAILED", ErrorKind.NETWORK, Delivery.NOT_SENT)
        assertTrue(error.detail("Cause").orEmpty().contains("SSLPeerUnverifiedException"), error.detail("Cause"))
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `a trusted matching certificate works`() =
        runBlocking {
            val (server, held) = tlsServer(listOf("127.0.0.1"))
            val trust = HandshakeCertificates.Builder().addTrustedCertificate(held.certificate).build()
            val http = testClient().newBuilder().sslSocketFactory(trust.sslSocketFactory(), trust.trustManager).build()
            server.enqueueJson("""{"status":"ok"}""")
            JournalApiClient(parse("https://127.0.0.1:${server.port}"), TEST_TOKEN, http).health()
        }

    // ------------------------------------------------------------------ after the request started

    @Test
    fun `a server that never answers is TIMEOUT and maybe sent`() {
        val server = startServer()
        val http = testClient().newBuilder().readTimeout(300, TimeUnit.MILLISECONDS).build()
        for (call in listOf<
            (
                JournalApiClient,
            ) -> suspend () -> Unit,
        >({ a -> { a.queue().let { } } }, { a -> { a.createTextEntry(textEntry()) } })) {
            server.enqueue(MockResponse.Builder().onResponseStart(SocketEffect.Stall).build())
            val error = failure(call(client(server, http)))
            error.assertCode("TIMEOUT", ErrorKind.NETWORK, Delivery.MAYBE_SENT)
            assertTrue(error.detail("Cause").orEmpty().contains("SocketTimeoutException"), error.detail("Cause"))
            assertTrue(error.hint.orEmpty().contains("NetBird"))
        }
    }

    @Test
    fun `a whole-call timeout after the request started is TIMEOUT and maybe sent`() {
        val server = startServer()
        server.enqueue(MockResponse.Builder().onResponseStart(SocketEffect.Stall).build())
        val http = testClient().newBuilder().callTimeout(400, TimeUnit.MILLISECONDS).build()
        failure { client(server, http).runBatch() }.assertCode("TIMEOUT", ErrorKind.NETWORK, Delivery.MAYBE_SENT)
    }

    @Test
    fun `a connection dropped after the request is CONNECTION_LOST and maybe sent`() {
        val server = startServer()
        server.enqueue(MockResponse.Builder().onResponseStart(SocketEffect.CloseSocket()).build())
        server.enqueue(MockResponse.Builder().onResponseStart(SocketEffect.CloseSocket()).build())
        val api = client(server)
        val post = failure { api.createTextEntry(textEntry()) }
        post.assertCode("CONNECTION_LOST", ErrorKind.NETWORK, Delivery.MAYBE_SENT)
        assertTrue(post.detail("Cause").orEmpty().isNotBlank())
        failure { api.facets() }.assertCode("CONNECTION_LOST", ErrorKind.NETWORK, Delivery.MAYBE_SENT)
        // A write is never replayed behind the caller's back: exactly one request per call reached the server.
        assertEquals(2, server.requestCount)
    }

    @Test
    fun `a body cut short is CONNECTION_LOST`() {
        val server = startServer()
        server.enqueue(
            MockResponse
                .Builder()
                .addHeader(
                    "Content-Type",
                    "application/json",
                ).body("""{"items":[],"total":""" + "0".repeat(10))
                .onResponseBody(SocketEffect.CloseSocket())
                .build(),
        )
        val error = failure { client(server).listEntries(EntryFilters.NONE, SortOrder.NEWEST_FIRST, 10, 0) }
        assertEquals(Delivery.MAYBE_SENT, error.delivery)
        assertTrue(error.code == "CONNECTION_LOST" || error.code == "RESPONSE_INVALID", error.code)
    }

    // ------------------------------------------------------------------ cancellation

    @Test
    fun `cancelling the coroutine cancels the call`() =
        runBlocking {
            val server = startServer()
            server.enqueue(MockResponse.Builder().onResponseStart(SocketEffect.Stall).build())
            val deferred = async(Dispatchers.Default) { client(server).queue() }
            assertTrue(server.takeRequest(5, TimeUnit.SECONDS) != null, "request reached the server")
            withTimeout(5_000) { deferred.cancelAndJoin() }
            assertTrue(deferred.isCancelled)
        }
}
