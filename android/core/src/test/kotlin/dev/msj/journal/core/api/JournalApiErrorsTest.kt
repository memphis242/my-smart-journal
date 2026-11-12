package dev.msj.journal.core.api

import mockwebserver3.MockResponse
import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

internal class JournalApiErrorsTest : ApiTestBase() {
    private fun listCall() = failure { api.getEntry(ENTRY_ID) }

    // ------------------------------------------------------------------ server problems

    @Test
    fun `the server's code wins and its detail becomes the message`() {
        server.enqueueProblem(401, "Unauthorized", "The access token was replaced.", "TOKEN_REISSUE_REQUIRED")
        val error = listCall()
        error.assertCode("TOKEN_REISSUE_REQUIRED", ErrorKind.HTTP, Delivery.RECEIVED)
        assertEquals(401, error.status)
        assertEquals("GET", error.method)
        assertEquals("/entries/$ENTRY_ID", error.path)
        assertEquals("Load entry failed: The access token was replaced.", error.message)
        assertTrue(error.hint.orEmpty().contains("msj create-token"))
        assertEquals("GET /api/v1/entries/$ENTRY_ID", error.detail("Request"))
        assertEquals("401 Client Error", error.detail("Status"))
        assertEquals("Unauthorized", error.detail("Title"))
        assertNull(error.detail("Type"))
        assertNull(error.detail("Body"))
    }

    @Test
    fun `auth codes pass through unchanged`() {
        for (code in listOf("UNAUTHORIZED", "TOKEN_REISSUE_REQUIRED", "BAD_TOKEN")) {
            server.enqueueProblem(401, "Unauthorized", "Nope.", code)
            val error = failure { api.checkSession() }
            error.assertCode(code, ErrorKind.HTTP, Delivery.RECEIVED)
            assertNotNull(error.hint, code)
        }
    }

    @Test
    fun `statuses without a code map to the web client's codes`() {
        val expected =
            mapOf(
                400 to "BAD_REQUEST",
                401 to "UNAUTHORIZED",
                403 to "FORBIDDEN",
                404 to "NOT_FOUND",
                409 to "CONFLICT",
                413 to "PAYLOAD_TOO_LARGE",
                415 to "UNSUPPORTED_MEDIA_TYPE",
                422 to "VALIDATION_ERROR",
                429 to "RATE_LIMITED",
                502 to "SERVER_UNAVAILABLE",
                503 to "SERVER_UNAVAILABLE",
                504 to "SERVER_UNAVAILABLE",
                500 to "SERVER_ERROR",
                507 to "SERVER_ERROR",
                418 to "HTTP_418",
                410 to "HTTP_410",
            )
        for ((status, code) in expected) {
            server.enqueueProblem(status, "Problem", "Detail for $status.")
            val error = listCall()
            error.assertCode(code, ErrorKind.HTTP, Delivery.RECEIVED)
            assertEquals(status, error.status)
        }
    }

    @Test
    fun `a message without detail falls back to the title and then the status`() {
        server.enqueueJson("""{"title":"Not Found","status":404}""", 404, PROBLEM_JSON)
        assertEquals("Load entry failed: Not Found", listCall().message)
        server.enqueueJson("""{"type":"about:blank"}""", 404)
        assertTrue(listCall().message.startsWith("Load entry failed: HTTP 404"))
    }

    @Test
    fun `validation issues become a Fields detail`() {
        val errors =
            ""","errors":[{"field":"query.limit","type":"less_than_equal"},{"type":"missing"},""" +
                """{"field":"body.date","type":"x","message":"bad"}]"""
        server.enqueueProblem(422, "Validation Error", "Request parameters are invalid.", "VALIDATION_ERROR", errors)
        val error = failure { api.listEntries(EntryFilters.NONE, SortOrder.NEWEST_FIRST, 10, 0) }
        error.assertCode("VALIDATION_ERROR", ErrorKind.HTTP, Delivery.RECEIVED)
        assertEquals(
            "query.limit: less_than_equal\n(unnamed field): missing\nbody.date: x (bad)",
            error.detail("Fields"),
        )
        assertTrue(error.hint.orEmpty().contains("fields listed"))
    }

    @Test
    fun `a custom problem type is shown but about blank is not`() {
        server.enqueueProblem(404, "Not Found", "Gone.", "ENTRY_NOT_FOUND", extra = ""","type2":1""")
        assertNull(listCall().detail("Type"))
        server.enqueueJson(
            """{"type":"https://errors.example/gone","title":"Not Found","detail":"Gone.","code":"ENTRY_NOT_FOUND"}""",
            404,
            PROBLEM_JSON,
        )
        assertEquals("https://errors.example/gone", listCall().detail("Type"))
    }

    @Test
    fun `rate limiting reads Retry-After as seconds`() {
        server.enqueueProblem(
            429,
            "Too Many Requests",
            "Slow down.",
            "RATE_LIMITED",
            headers =
                mapOf("Retry-After" to "42"),
        )
        val error = failure { api.checkSession() }
        error.assertCode("RATE_LIMITED", ErrorKind.HTTP, Delivery.RECEIVED)
        assertEquals(42L, error.retryAfterS)
        assertEquals("42 s", error.detail("Retry after"))
        assertTrue(error.hint.orEmpty().contains("Wait 42 s"))
    }

    @Test
    fun `rate limiting reads Retry-After as an HTTP date against the server clock`() {
        server.enqueueProblem(
            429,
            "Too Many Requests",
            "Slow down.",
            "RATE_LIMITED",
            headers =
                mapOf(
                    "Date" to "Wed, 11 Nov 2026 10:00:00 GMT",
                    "Retry-After" to "Wed, 11 Nov 2026 10:00:30 GMT",
                ),
        )
        assertEquals(30L, failure { api.checkSession() }.retryAfterS)
    }

    @Test
    fun `an unparseable or past Retry-After is handled`() {
        server.enqueueProblem(
            429,
            "Too Many Requests",
            "Slow down.",
            "RATE_LIMITED",
            headers =
                mapOf("Retry-After" to "soon"),
        )
        val unknown = failure { api.checkSession() }
        assertNull(unknown.retryAfterS)
        assertTrue(unknown.hint.orEmpty().contains("Wait a minute"))
        server.enqueueProblem(
            429,
            "Too Many Requests",
            "Slow down.",
            "RATE_LIMITED",
            headers =
                mapOf(
                    "Date" to "Wed, 11 Nov 2026 10:00:00 GMT",
                    "Retry-After" to "Wed, 11 Nov 2026 09:00:00 GMT",
                ),
        )
        assertEquals(0L, failure { api.checkSession() }.retryAfterS)
    }

    @Test
    fun `ENTRY_CHANGED carries the current version`() {
        server.enqueueProblem(
            409,
            "Conflict",
            "The entry changed since version 3.",
            "ENTRY_CHANGED",
            ""","current_version":4""",
        )
        val error =
            failure { api.patchEntry(ENTRY_ID, EntryPatch(title = EntryPatch.Field.Set("x"), expectedVersion = 3)) }
        error.assertCode("ENTRY_CHANGED", ErrorKind.HTTP, Delivery.RECEIVED)
        assertEquals(4, error.currentVersion)
        assertEquals(409, error.status)
        assertTrue(error.hint.orEmpty().contains("reapply"))
    }

    @Test
    fun `current_version is ignored for other codes and when mistyped`() {
        server.enqueueProblem(409, "Conflict", "Other.", "CONFLICT", ""","current_version":4""")
        assertNull(listCall().currentVersion)
        server.enqueueProblem(409, "Conflict", "Changed.", "ENTRY_CHANGED", ""","current_version":"4"""")
        assertNull(listCall().currentVersion)
    }

    @Test
    fun `an already running batch is a coded rejection`() {
        server.enqueueProblem(409, "Conflict", "A batch is already running.", "BATCH_ALREADY_RUNNING")
        val error = failure { api.runBatch() }
        error.assertCode("BATCH_ALREADY_RUNNING", ErrorKind.HTTP, Delivery.RECEIVED)
        assertEquals("POST", error.method)
        assertTrue(error.hint.orEmpty().contains("again when the current one finishes"))
    }

    @Test
    fun `upload rejections carry their hints`() {
        server.enqueueProblem(413, "Payload Too Large", "Audio exceeds the configured upload limit.", null)
        server.enqueueProblem(415, "Unsupported Media Type", "Allowed audio types are WAV, MP3.", null)
        val file =
            java.io.File
                .createTempFile("rec", ".ogg")
                .apply { writeBytes(ByteArray(8)) }
        try {
            val entry = NewAudioEntry(file, "audio/ogg", "r.ogg", null, emptyList(), "2026-11-11")
            val tooLarge = failure { api.createAudioEntry(entry) }
            tooLarge.assertCode("PAYLOAD_TOO_LARGE", ErrorKind.HTTP, Delivery.RECEIVED)
            assertTrue(tooLarge.message.startsWith("Upload recording failed:"))
            assertTrue(tooLarge.hint.orEmpty().contains("shorter recording"))
            failure {
                api.createAudioEntry(
                    entry,
                )
            }.assertCode("UNSUPPORTED_MEDIA_TYPE", ErrorKind.HTTP, Delivery.RECEIVED)
        } finally {
            file.delete()
        }
    }

    // ------------------------------------------------------------------ proxy pages and delivery

    @Test
    fun `a 502 from a proxy page may have reached the server`() {
        server.enqueue(
            MockResponse
                .Builder()
                .code(
                    502,
                ).addHeader("Content-Type", "text/html")
                .body("<html><h1>Bad Gateway</h1>nginx</html>")
                .build(),
        )
        val error = failure { api.createTextEntry(NewTextEntry("Hello", null, emptyList(), "2026-11-11")) }
        error.assertCode("SERVER_UNAVAILABLE", ErrorKind.HTTP, Delivery.MAYBE_SENT)
        assertEquals(502, error.status)
        assertTrue(error.detail("Body").orEmpty().contains("Bad Gateway"))
        assertTrue(error.hint.orEmpty().contains("down or restarting"))
    }

    @Test
    fun `a 502 problem from the journal itself was received`() {
        server.enqueueProblem(502, "Bad Gateway", "Upstream failed.", "BAD_GATEWAY_UPSTREAM")
        server.enqueueProblem(503, "Service Unavailable", "Restarting.")
        failure { api.runBatch() }.assertCode("BAD_GATEWAY_UPSTREAM", ErrorKind.HTTP, Delivery.RECEIVED)
        failure { api.runBatch() }.assertCode("SERVER_UNAVAILABLE", ErrorKind.HTTP, Delivery.RECEIVED)
    }

    @Test
    fun `any status with a non-problem body is MAYBE_SENT and the body excerpt is bounded`() {
        server.enqueue(
            MockResponse
                .Builder()
                .code(404)
                .body("x".repeat(5000))
                .build(),
        )
        server.enqueue(MockResponse.Builder().code(500).build())
        val big = listCall()
        big.assertCode("NOT_FOUND", ErrorKind.HTTP, Delivery.MAYBE_SENT)
        assertTrue(big.detail("Body").orEmpty().length < 1400)
        val empty = listCall()
        empty.assertCode("SERVER_ERROR", ErrorKind.HTTP, Delivery.MAYBE_SENT)
        assertNull(empty.detail("Body"))
    }

    @Test
    fun `the token is removed when the server echoes it`() {
        server.enqueueProblem(401, "Unauthorized", "Bad credentials: Bearer $TEST_TOKEN", "BAD_TOKEN")
        server.enqueue(
            MockResponse
                .Builder()
                .code(502)
                .body("<html>proxy saw Authorization: Bearer $TEST_TOKEN</html>")
                .build(),
        )
        val problem = failure { api.checkSession() }
        assertTrue(problem.message.contains("[token]"), problem.message)
        val proxy = failure { api.checkSession() }
        assertTrue(proxy.detail("Body").orEmpty().contains("[token]"))
    }

    // ------------------------------------------------------------------ malformed answers

    @Test
    fun `a missing field is RESPONSE_INVALID and names the path`() {
        server.enqueueJson("""{"items":[{"id":"x"}],"total":1}""")
        val error = failure { api.listEntries(EntryFilters.NONE, SortOrder.NEWEST_FIRST, 10, 0) }
        error.assertCode("RESPONSE_INVALID", ErrorKind.RESPONSE, Delivery.RECEIVED)
        assertEquals("GET /api/v1/entries", error.detail("Request"))
        val cause = error.detail("Cause").orEmpty()
        assertTrue(cause.contains("entry_date"), cause)
        assertTrue(cause.contains("$.items[0]"), cause)
        assertTrue(error.message.startsWith("Load entries failed:"))
        assertTrue(error.hint.orEmpty().contains("disagree"))
    }

    @Test
    fun `a mistyped field and non-JSON bodies are RESPONSE_INVALID`() {
        server.enqueueJson("""{"items":[],"total":"many"}""")
        server.enqueueJson("<html>login</html>", type = "text/html")
        server.enqueueJson("")
        repeat(3) {
            failure { api.listEntries(EntryFilters.NONE, SortOrder.NEWEST_FIRST, 10, 0) }
                .assertCode("RESPONSE_INVALID", ErrorKind.RESPONSE, Delivery.RECEIVED)
        }
    }

    @Test
    fun `an unknown extra field is ignored but an unknown enum value is invalid`() {
        server.enqueueJson("""{"items":[],"total":0,"future":true}""")
        assertEquals(
            0,
            kotlinx.coroutines.runBlocking {
                api.listEntries(EntryFilters.NONE, SortOrder.NEWEST_FIRST, 10, 0).total
            },
        )
        server.enqueueJson(SUMMARY_JSON.replace("\"done\"", "\"vanished\"").let { """{"items":[$it],"total":1}""" })
        failure { api.listEntries(EntryFilters.NONE, SortOrder.NEWEST_FIRST, 10, 0) }
            .assertCode("RESPONSE_INVALID", ErrorKind.RESPONSE, Delivery.RECEIVED)
    }

    @Test
    fun `session answers other than 204 are RESPONSE_INVALID`() {
        server.enqueueJson("<html>ok</html>", type = "text/html")
        failure { api.checkSession() }.assertCode("RESPONSE_INVALID", ErrorKind.RESPONSE, Delivery.RECEIVED)
    }

    // ------------------------------------------------------------------ health

    @Test
    fun `health accepts only the ok body`() {
        server.enqueueJson("""{"status":"ok"}""")
        kotlinx.coroutines.runBlocking { api.health() }
    }

    @Test
    fun `health rejects everything else as not a journal server`() {
        val answers =
            listOf(
                MockResponse
                    .Builder()
                    .code(404)
                    .body("<html>404 page not found</html>")
                    .build(),
                MockResponse.Builder().json("""{"status":"degraded"}""").build(),
                MockResponse.Builder().json("""{"ok":true}""").build(),
                MockResponse.Builder().json("""["ok"]""").build(),
                MockResponse
                    .Builder()
                    .code(
                        200,
                    ).addHeader("Content-Type", "text/html")
                    .body("<html>Welcome to nginx</html>")
                    .build(),
                MockResponse.Builder().code(200).build(),
                MockResponse.Builder().json("""{"status":"ok"}""", code = 201).build(),
                MockResponse.Builder().json("""{"status":"ok"}""", code = 503).build(),
            )
        for (answer in answers) {
            server.enqueue(answer)
            val error = failure { api.health() }
            error.assertCode("NOT_A_JOURNAL_SERVER", ErrorKind.RESPONSE, Delivery.RECEIVED)
            assertNotNull(error.status)
            assertEquals("GET /api/v1/health", error.detail("Request"))
            assertNotNull(error.detail("Status"))
            assertTrue(error.hint.orEmpty().contains("https://journal.example.com"))
        }
    }

    @Test
    fun `health shows a bounded body excerpt`() {
        server.enqueue(
            MockResponse
                .Builder()
                .code(200)
                .body("<html>" + "y".repeat(4000) + "</html>")
                .build(),
        )
        val error = failure { api.health() }
        assertTrue(error.detail("Body").orEmpty().startsWith("<html>yyy"))
        assertTrue(error.detail("Body").orEmpty().length < 1400)
    }
}
