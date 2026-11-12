package dev.msj.journal.core.api

import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Before
import java.net.InetAddress
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.fail

internal const val TEST_TOKEN = "tok-SECRET-0123456789abcdef"
internal const val ENTRY_ID = "6f1c0f3e9a2b4c7d8e9f0a1b2c3d4e5f"
internal const val PROBLEM_JSON = "application/problem+json"

internal val SUMMARY_JSON =
    """
    {"id":"$ENTRY_ID","entry_date":"2026-11-07","created_at":"2026-11-07T21:04:33.120415+00:00",
     "title":"Demo night","tags":["school"],"source":"text","summary":"Demoed Kestrel KV to the class.",
     "text":"Demo done. Kestrel KV ran in front of the class.","projects":["Kestrel KV"],"skills":["Raft","Rust"],
     "status":"done","revision":1,"version":3}
    """.trimIndent().replace("\n", "")

internal val DETAIL_JSON =
    """
    {"id":"$ENTRY_ID","entry_date":"2026-11-07","created_at":"2026-11-07T21:04:33.120415+00:00",
     "title":"Demo night","tags":["school"],"source":"text","summary":"Demoed Kestrel KV to the class.",
     "text":"Demo done.","projects":[],"skills":[],"status":"done","revision":1,"version":3,
     "needs_attention":false,"compilation_count":0,
     "artifacts":[{"id":"a1","kind":"user_text","content":"Demo done.","created_at":"2026-11-07T21:04:33+00:00","revision":1}],
     "jobs":[]}
    """.trimIndent().replace("\n", "")

internal fun problemJson(
    status: Int,
    title: String,
    detail: String,
    code: String? = null,
    extra: String = "",
): String {
    val codePart = if (code == null) "" else ""","code":"$code""""
    return """{"type":"about:blank","title":"$title","status":$status,"detail":"$detail"$codePart$extra}"""
}

internal fun MockResponse.Builder.json(
    body: String,
    code: Int = 200,
    type: String = "application/json",
): MockResponse.Builder = code(code).addHeader("Content-Type", type).body(body)

internal fun MockWebServer.enqueueJson(
    body: String,
    code: Int = 200,
    type: String = "application/json",
) = enqueue(MockResponse.Builder().json(body, code, type).build())

internal fun MockWebServer.enqueueProblem(
    status: Int,
    title: String,
    detail: String,
    code: String? = null,
    extra: String = "",
    headers: Map<String, String> = emptyMap(),
) {
    val builder = MockResponse.Builder().json(problemJson(status, title, detail, code, extra), status, PROBLEM_JSON)
    headers.forEach { (name, value) -> builder.addHeader(name, value) }
    enqueue(builder.build())
}

internal fun testClient(): OkHttpClient = OkHttpClient.Builder().retryOnConnectionFailure(false).build()

internal fun serverUrlFor(
    server: MockWebServer,
    path: String = "",
): ServerUrl {
    val text = "http://${server.hostName}:${server.port}$path"
    val result = ServerUrl.parse(text, setOf(server.hostName))
    return assertIs<ServerUrlResult.Valid>(result, "test server URL $text").url
}

/** Asserts the secret is nowhere in the error or the exception built from it. */
internal fun assertNoToken(exception: ApiException) {
    val error = exception.error
    val texts =
        buildList {
            add(error.message)
            error.hint?.let(::add)
            error.details.forEach {
                add(it.label)
                add(it.value)
            }
            add(exception.message.orEmpty())
            add(exception.toString())
        }
    texts.forEach { assertFalse(it.contains(TEST_TOKEN), "token leaked into: $it") }
}

/** Runs [block], expects an [ApiException], checks it does not leak the token, and returns its error. */
internal fun failure(block: suspend () -> Unit): ApiError =
    runBlocking {
        try {
            block()
        } catch (e: ApiException) {
            assertNoToken(e)
            return@runBlocking e.error
        }
        fail("expected an ApiException, but the call succeeded")
    }

internal fun ApiError.detail(label: String): String? = details.firstOrNull { it.label == label }?.value

internal fun ApiError.assertCode(
    code: String,
    kind: ErrorKind,
    delivery: Delivery,
) {
    assertEquals(code, this.code, "code of: $this")
    assertEquals(kind, this.kind, "kind of: $this")
    assertEquals(delivery, this.delivery, "delivery of: $this")
}

/** A MockWebServer on loopback plus a client for it. */
internal abstract class ApiTestBase {
    protected val server = MockWebServer()
    protected lateinit var api: JournalApiClient

    @Before
    fun startServer() {
        server.start(InetAddress.getByName("127.0.0.1"), 0)
        api = JournalApiClient(serverUrlFor(server), TEST_TOKEN, testClient())
    }

    @After
    fun stopServer() {
        server.close()
    }
}
