package dev.msj.journal.core.api

import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import mockwebserver3.MockResponse
import org.junit.Test
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertIs
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

private fun tierLine(
    tier: Int,
    name: String,
    results: String = "[]",
    extra: String = "",
) = """{"tier":$tier,"name":"$name","results":$results,"elapsed_ms":0.5$extra}"""

private const val DONE_LINE = """{"done":true,"elapsed_ms":2.6}"""

private val HIT = """[{"entry_id":"$ENTRY_ID","score":1.0,"snippet":"Demo done.","entry":$SUMMARY_JSON}]"""

/** A one-connection socket server with a scripted response, for streaming behavior MockWebServer cannot pace. */
private class RawServer(
    private val script: (Socket, OutputStream) -> Unit,
) : AutoCloseable {
    private val listener = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))
    val port: Int get() = listener.localPort
    private var connection: Socket? = null
    private val worker =
        thread(isDaemon = true) {
            try {
                listener.accept().use { socket ->
                    connection = socket
                    readRequestHead(socket)
                    script(socket, socket.getOutputStream())
                }
            } catch (_: java.io.IOException) {
                // The test closed the server or the client hung up; nothing to report.
            }
        }

    private fun readRequestHead(socket: Socket) {
        val input = socket.getInputStream()
        var tail = 0
        while (tail != HEAD_END) {
            val b = input.read()
            if (b < 0) return
            tail = (tail shl 8) or b
        }
    }

    override fun close() {
        listener.close()
        connection?.close()
        worker.join(5_000)
    }

    private companion object {
        const val HEAD_END = 0x0D0A0D0A
    }
}

private const val NDJSON_HEAD =
    "HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nTransfer-Encoding: chunked\r\n\r\n"

private fun OutputStream.chunk(text: String) {
    val bytes = text.toByteArray(Charsets.UTF_8)
    write("${bytes.size.toString(16)}\r\n".toByteArray())
    write(bytes)
    write("\r\n".toByteArray())
    flush()
}

private fun OutputStream.head() {
    write(NDJSON_HEAD.toByteArray())
    flush()
}

internal class JournalApiSearchTest : ApiTestBase() {
    private fun streamOf(vararg lines: String) = lines.joinToString("\n", postfix = "\n")

    private fun enqueueStream(vararg lines: String) =
        server.enqueue(
            MockResponse
                .Builder()
                .addHeader("Content-Type", "application/x-ndjson")
                .body(streamOf(*lines))
                .build(),
        )

    private fun collect(
        query: String = "Demo",
        tiers: List<Int> = listOf(1, 2, 3, 4, 5),
    ): List<SearchEvent> = runBlocking { withTimeout(15_000) { api.search(query, EntryFilters.NONE, tiers).toList() } }

    private fun collectFailure(): ApiError = failure { api.search("Demo", EntryFilters.NONE, listOf(1, 2)).toList() }

    // ------------------------------------------------------------------ request and decoding

    @Test
    fun `the request carries the query tiers filters and bearer token`() {
        enqueueStream(tierLine(1, "exact"), DONE_LINE)
        runBlocking {
            api
                .search(
                    "kestrel & raft",
                    EntryFilters(from = "2026-11-01", tags = listOf("a", "b"), skills = listOf("Rust")),
                    listOf(1, 2, 3),
                ).toList()
        }
        val request = assertNotNull(server.takeRequest(5, TimeUnit.SECONDS))
        assertEquals("/api/v1/search", request.url.encodedPath)
        assertEquals("kestrel & raft", request.url.queryParameter("q"))
        assertEquals("1,2,3", request.url.queryParameter("tiers"))
        assertEquals(listOf("a", "b"), request.url.queryParameterValues("tag"))
        assertEquals("2026-11-01", request.url.queryParameter("from"))
        assertEquals(listOf("Rust"), request.url.queryParameterValues("skill"))
        assertEquals("Bearer $TEST_TOKEN", request.headers["Authorization"])
        assertTrue(request.headers["Accept"].orEmpty().contains("ndjson"))
    }

    @Test
    fun `tiers and the completion line decode with embedded summaries`() {
        enqueueStream(
            tierLine(1, "exact", HIT),
            tierLine(2, "substring"),
            tierLine(
                5,
                "semantic",
                "[]",
                ""","unavailable":{"code":"EMBEDDING_MODEL_LOAD_FAILED","detail":"The configured embedding model could not load."}""",
            ),
            DONE_LINE,
        )
        val events = collect()
        assertEquals(4, events.size)
        val first = assertIs<SearchEvent.Tier>(events[0])
        assertEquals(1, first.tier)
        assertEquals(
            "Demo night",
            first.results
                .single()
                .entry
                ?.title,
        )
        assertEquals("Demo done.", first.results.single().snippet)
        assertNull(first.unavailable)
        assertEquals("EMBEDDING_MODEL_LOAD_FAILED", assertIs<SearchEvent.Tier>(events[2]).unavailable?.code)
        assertEquals(SearchEvent.Done(2.6, null), events[3])
    }

    @Test
    fun `a failed stream's coded error arrives as the completion event`() {
        enqueueStream(
            tierLine(1, "exact"),
            """{"done":true,"error":{"status":500,"code":"INTERNAL_ERROR","detail":"Search failed; check storage and service logs."}}""",
        )
        val done = assertIs<SearchEvent.Done>(collect().last())
        assertNull(done.elapsedMs)
        assertEquals(
            SearchEvent.StreamError(500, "INTERNAL_ERROR", "Search failed; check storage and service logs."),
            done.error,
        )
    }

    @Test
    fun `the flow ends after the completion line`() {
        enqueueStream(tierLine(1, "exact"), DONE_LINE, "not even json")
        assertEquals(2, collect().size)
    }

    @Test
    fun `an HTTP error before streaming is the coded problem`() {
        server.enqueueProblem(422, "Validation Error", "Request parameters are invalid.", "INVALID_QUERY")
        server.enqueueProblem(401, "Unauthorized", "Sign in.", "UNAUTHORIZED")
        failure { api.search("x", EntryFilters.NONE, listOf(1)).toList() }
            .assertCode("INVALID_QUERY", ErrorKind.HTTP, Delivery.RECEIVED)
        failure { api.search("x", EntryFilters.NONE, listOf(1)).toList() }
            .assertCode("UNAUTHORIZED", ErrorKind.HTTP, Delivery.RECEIVED)
    }

    // ------------------------------------------------------------------ stream failures

    @Test
    fun `a stream that ends without a completion line is STREAM_INCOMPLETE`() {
        enqueueStream(tierLine(1, "exact"), tierLine(2, "substring"))
        val error = collectFailure()
        error.assertCode("STREAM_INCOMPLETE", ErrorKind.RESPONSE, Delivery.RECEIVED)
        assertEquals(200, error.status)
        assertTrue(error.hint.orEmpty().contains("Search again"))
    }

    @Test
    fun `an empty stream is STREAM_INCOMPLETE`() {
        server.enqueue(MockResponse.Builder().build())
        collectFailure().assertCode("STREAM_INCOMPLETE", ErrorKind.RESPONSE, Delivery.RECEIVED)
    }

    @Test
    fun `malformed lines are RESPONSE_INVALID naming the line`() {
        val cases =
            listOf(
                streamOf(tierLine(1, "exact"), "{broken json", DONE_LINE) to "2",
                streamOf(tierLine(1, "exact"), "", """{"hello":"world"}""", DONE_LINE) to "3",
                streamOf("[1,2]") to "1",
                streamOf(tierLine(9, "bogus")) to "1",
                streamOf("""{"tier":1,"name":"exact","results":"none"}""") to "1",
                streamOf("""{"done":true,"error":{"status":500}}""") to "1",
            )
        for ((body, line) in cases) {
            server.enqueue(MockResponse.Builder().body(body).build())
            val error = collectFailure()
            error.assertCode("RESPONSE_INVALID", ErrorKind.RESPONSE, Delivery.RECEIVED)
            assertEquals(line, error.detail("Stream line"), body)
            assertEquals("GET /api/v1/search", error.detail("Request"))
        }
    }

    // ------------------------------------------------------------------ incremental delivery

    @Test
    fun `the first tier arrives while the server is still holding the rest`() {
        val release = CountDownLatch(1)
        // True only if the client's first emitted tier is what released the server.
        val releasedByFirstTier = AtomicBoolean(false)
        RawServer { _, out ->
            out.head()
            out.chunk(tierLine(1, "exact", HIT) + "\n")
            releasedByFirstTier.set(release.await(15, TimeUnit.SECONDS))
            out.chunk(tierLine(2, "substring") + "\n")
            out.chunk(DONE_LINE + "\n")
            out.write("0\r\n\r\n".toByteArray())
            out.flush()
        }.use { raw ->
            val api = JournalApiClient(parseLoopback(raw.port), TEST_TOKEN, testClient())
            val seen = mutableListOf<SearchEvent>()
            runBlocking {
                withTimeout(20_000) {
                    api.search("Demo", EntryFilters.NONE, listOf(1, 2)).collect { event ->
                        seen += event
                        // The server holds the rest back until this first tier has been delivered.
                        if (seen.size == 1) release.countDown()
                    }
                }
            }
            assertTrue(releasedByFirstTier.get(), "tier 1 was emitted before the server sent the rest")
            assertEquals(listOf(1, 2), seen.filterIsInstance<SearchEvent.Tier>().map { it.tier })
            assertIs<SearchEvent.Done>(seen.last())
        }
    }

    @Test
    fun `a connection lost mid-stream is STREAM_INTERRUPTED after the tiers already received`() {
        RawServer { _, out ->
            out.head()
            out.chunk(tierLine(1, "exact", HIT) + "\n")
        }.use { raw ->
            val api = JournalApiClient(parseLoopback(raw.port), TEST_TOKEN, testClient())
            val seen = mutableListOf<SearchEvent>()
            val error =
                failure {
                    api.search("Demo", EntryFilters.NONE, listOf(1, 2)).collect { seen += it }
                }
            error.assertCode("STREAM_INTERRUPTED", ErrorKind.NETWORK, Delivery.RECEIVED)
            assertEquals(1, seen.size)
            assertEquals(200, error.status)
            assertTrue(error.detail("Cause").orEmpty().isNotBlank())
            assertTrue(error.hint.orEmpty().contains("Search again"))
        }
    }

    @Test
    fun `cancelling the collector closes the connection`() {
        val serverSawClose = CountDownLatch(1)
        RawServer { socket, out ->
            out.head()
            out.chunk(tierLine(1, "exact") + "\n")
            // The client sends nothing more; end-of-input means it closed the connection.
            socket.soTimeout = 15_000
            try {
                if (socket.getInputStream().read() < 0) serverSawClose.countDown()
            } catch (_: java.io.IOException) {
                serverSawClose.countDown()
            }
        }.use { raw ->
            val api = JournalApiClient(parseLoopback(raw.port), TEST_TOKEN, testClient())
            val first = runBlocking { withTimeout(10_000) { api.search("Demo", EntryFilters.NONE, listOf(1)).first() } }
            assertEquals(1, assertIs<SearchEvent.Tier>(first).tier)
            assertTrue(
                serverSawClose.await(10, TimeUnit.SECONDS),
                "the server saw the connection close after the collector stopped",
            )
        }
    }

    @Test
    fun `search assertions stay eager`() {
        assertFailsWith<AssertionError> { api.search("", EntryFilters.NONE, listOf(1)) }
    }

    private fun parseLoopback(port: Int): ServerUrl =
        (ServerUrl.parse("http://127.0.0.1:$port", setOf("127.0.0.1")) as ServerUrlResult.Valid).url
}
