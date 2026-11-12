package dev.msj.journal.core.api

import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import org.junit.Test
import java.io.File
import java.time.Instant
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

internal class JournalApiRequestsTest : ApiTestBase() {
    private fun takeRequest() =
        assertNotNull(server.takeRequest(5, java.util.concurrent.TimeUnit.SECONDS), "a request reached the server")

    // ------------------------------------------------------------------ auth and base path

    @Test
    fun `bearer token is sent on every call except health`() =
        runBlocking {
            server.enqueueJson("""{"status":"ok"}""")
            server.enqueue(MockResponse.Builder().code(204).build())
            server.enqueueJson("""{"tags":[],"projects":[],"skills":[]}""")
            api.health()
            api.checkSession()
            api.facets()
            val health = takeRequest()
            assertEquals("/api/v1/health", health.url.encodedPath)
            assertNull(health.headers["Authorization"])
            assertEquals("Bearer $TEST_TOKEN", takeRequest().headers["Authorization"])
            assertEquals("Bearer $TEST_TOKEN", takeRequest().headers["Authorization"])
        }

    @Test
    fun `a reverse-proxy mount path prefixes every call`() =
        runBlocking {
            val mounted = JournalApiClient(serverUrlFor(server, "/journal/api/v1/"), TEST_TOKEN, testClient())
            server.enqueue(MockResponse.Builder().code(204).build())
            mounted.checkSession()
            assertEquals("/journal/api/v1/session", takeRequest().url.encodedPath)
        }

    // ------------------------------------------------------------------ reads

    @Test
    fun `listEntries sends view order paging and repeated filters`() =
        runBlocking {
            server.enqueueJson("""{"items":[$SUMMARY_JSON],"total":1}""")
            val filters =
                EntryFilters(
                    from = "2026-11-01",
                    to = "2026-11-11",
                    tags = listOf("school", "kv & co"),
                    projects = listOf("Kestrel KV"),
                    skills = listOf("Raft", "Rust"),
                )
            val page = api.listEntries(filters, SortOrder.OLDEST_FIRST, limit = 50, offset = 100)
            assertEquals(1, page.total)
            assertEquals("Demo night", page.items.single().title)
            assertEquals(3, page.items.single().version)
            val url = takeRequest().url
            assertEquals("/api/v1/entries", url.encodedPath)
            assertEquals(listOf("summary"), url.queryParameterValues("view"))
            assertEquals(listOf("asc"), url.queryParameterValues("order"))
            assertEquals(listOf("50"), url.queryParameterValues("limit"))
            assertEquals(listOf("100"), url.queryParameterValues("offset"))
            assertEquals(listOf("2026-11-01"), url.queryParameterValues("from"))
            assertEquals(listOf("2026-11-11"), url.queryParameterValues("to"))
            assertEquals(listOf("school", "kv & co"), url.queryParameterValues("tag"))
            assertEquals(listOf("Kestrel KV"), url.queryParameterValues("project"))
            assertEquals(listOf("Raft", "Rust"), url.queryParameterValues("skill"))
        }

    @Test
    fun `listEntries omits unset filters`() =
        runBlocking {
            server.enqueueJson("""{"items":[],"total":0}""")
            api.listEntries(EntryFilters.NONE, SortOrder.NEWEST_FIRST, 20, 0)
            val url = takeRequest().url
            assertEquals(setOf("view", "order", "limit", "offset"), url.queryParameterNames)
            assertEquals("desc", url.queryParameter("order"))
        }

    @Test
    fun `listEntriesFull asks for the full view`() =
        runBlocking {
            server.enqueueJson("""{"items":[$DETAIL_JSON],"total":1}""")
            val page = api.listEntriesFull(EntryFilters(from = "2026-11-07", to = "2026-11-07"), 500, 0)
            assertEquals("Demo done.", page.items.single().rawText())
            val url = takeRequest().url
            assertEquals(listOf("full"), url.queryParameterValues("view"))
            assertEquals(listOf("500"), url.queryParameterValues("limit"))
        }

    @Test
    fun `ids are encoded as single path segments`() =
        runBlocking {
            repeat(2) { server.enqueueJson(DETAIL_JSON) }
            server.enqueueJson("""{"newer":null,"older":"abc"}""")
            api.getEntry("a/b c?x#y")
            api.patchEntry("../etc", EntryPatch(title = EntryPatch.Field.Set("t")))
            val neighbors = api.neighbors("a%b", EntryFilters(tags = listOf("t")))
            assertEquals("abc", neighbors.older)
            assertNull(neighbors.newer)
            assertEquals("/api/v1/entries/a%2Fb%20c%3Fx%23y", takeRequest().url.encodedPath)
            assertEquals("/api/v1/entries/..%2Fetc", takeRequest().url.encodedPath)
            val last = takeRequest().url
            assertEquals("/api/v1/entries/a%25b/neighbors", last.encodedPath)
            assertEquals(listOf("t"), last.queryParameterValues("tag"))
        }

    @Test
    fun `queue reports the server clock from the Date header`() =
        runBlocking {
            val date = "Wed, 11 Nov 2026 10:00:20 GMT"
            val body =
                """{"batch":{"running":false,"started_at":null,"trigger":null,"last_finished_at":"2026-11-08T01:02:03.000000+00:00",
                |"last_result":{"processed":9,"failed":0},"last_error":{"code":"BATCH_LEASE_LOST","detail":"Another worker took the batch lease."},
                |"request_pending":true},
                |"worker":{"last_seen_at":"2026-11-08T10:00:00+00:00","interval_s":86400.0,"next_run_at":"2026-11-09T01:00:00+00:00"},
                |"counts":{"queued":3,"running":0,"failed":1,"blocked":2},
                |"items":[{"entry_id":null,"entry_title":null,"entry_date":null,"job_id":"c0ffee","kind":"compile","target":null,"turn":null,
                |"status":"failed","blocked_by":[],"last_error":"HTTP 503","not_before":null}]}
                """.trimMargin()
                    .replace("\n", "")
            server.enqueue(
                MockResponse
                    .Builder()
                    .json(body)
                    .addHeader("Date", date)
                    .build(),
            )
            val status = api.queue()
            assertEquals(Instant.parse("2026-11-11T10:00:20Z").toEpochMilli(), status.serverTimeMs)
            assertTrue(status.snapshot.batch.requestPending)
            assertNull(status.snapshot.worker.stale)
            assertEquals(
                "BATCH_LEASE_LOST",
                status.snapshot.batch.lastError
                    ?.code,
            )
            assertNull(
                status.snapshot.items
                    .single()
                    .entryId,
            )
            assertEquals("/api/v1/queue", takeRequest().url.encodedPath)
        }

    @Test
    fun `queue tolerates a missing or unparseable Date header`() =
        runBlocking {
            val body =
                """{"batch":{"running":false},"worker":{},"counts":{"queued":0,"running":0,"failed":0,"blocked":0},"items":[]}"""
            server.enqueue(
                MockResponse
                    .Builder()
                    .json(body)
                    .setHeader("Date", "not a date")
                    .build(),
            )
            assertNull(api.queue().serverTimeMs)
        }

    // ------------------------------------------------------------------ batches

    @Test
    fun `runBatch posts and reads the stale-worker flag when present`() =
        runBlocking {
            server.enqueueJson("""{"requested":true}""", code = 202)
            server.enqueueJson("""{"requested":true,"worker":{"stale":true}}""", code = 202)
            assertNull(api.runBatch().worker)
            assertEquals(true, api.runBatch().worker?.stale)
            val request = takeRequest()
            assertEquals("POST", request.method)
            assertEquals("/api/v1/batches/run", request.url.encodedPath)
        }

    // ------------------------------------------------------------------ writes

    @Test
    fun `createTextEntry posts JSON with a null title`() =
        runBlocking {
            server.enqueueJson(DETAIL_JSON, code = 201)
            api.createTextEntry(NewTextEntry("Hello é", null, listOf("a", "b"), "2026-11-11"))
            val request = takeRequest()
            assertEquals("POST", request.method)
            assertEquals("/api/v1/entries", request.url.encodedPath)
            assertTrue(request.headers["Content-Type"].orEmpty().startsWith("application/json"))
            assertEquals(
                """{"text":"Hello é","title":null,"tags":["a","b"],"entry_date":"2026-11-11"}""".replace(
                    "\\u00e9",
                    "é",
                ),
                request.body?.utf8(),
            )
        }

    @Test
    fun `createAudioEntry streams a multipart form with a bare MIME type`() =
        runBlocking {
            val file =
                File
                    .createTempFile(
                        "rec",
                        ".ogg",
                    ).apply { writeBytes(byteArrayOf(0x4f, 0x67, 0x67, 0x53, 0, 1, 2, 3)) }
            try {
                server.enqueueJson(DETAIL_JSON, code = 201)
                api.createAudioEntry(
                    NewAudioEntry(file, "audio/ogg", "rec-1.ogg", "Morning", listOf("voice"), "2026-11-11"),
                )
                val request = takeRequest()
                val contentType = request.headers["Content-Type"].orEmpty()
                assertTrue(contentType.startsWith("multipart/form-data; boundary="), contentType)
                val body = request.body!!.utf8()
                assertTrue(body.contains("""name="file"; filename="rec-1.ogg""""), body)
                assertTrue(body.contains("Content-Type: audio/ogg\r\n"), body)
                assertTrue(!body.contains("audio/ogg;"), "no parameters on the part type")
                assertTrue(body.contains("name=\"title\"\r\n\r\nMorning\r\n"), body)
                assertTrue(body.contains("name=\"tags\"\r\n\r\n[\"voice\"]\r\n"), body)
                assertTrue(body.contains("name=\"entry_date\"\r\n\r\n2026-11-11\r\n"), body)
                assertTrue(body.contains("OggS"))
            } finally {
                file.delete()
            }
        }

    @Test
    fun `createAudioEntry omits a null title`() =
        runBlocking {
            val file = File.createTempFile("rec", ".webm").apply { writeBytes(ByteArray(4)) }
            try {
                server.enqueueJson(DETAIL_JSON, code = 201)
                api.createAudioEntry(NewAudioEntry(file, "audio/webm", "r.webm", null, emptyList(), "2026-11-11"))
                val body = takeRequest().body!!.utf8()
                assertTrue(!body.contains("name=\"title\""), body)
                assertTrue(body.contains("name=\"tags\"\r\n\r\n[]\r\n"), body)
            } finally {
                file.delete()
            }
        }

    @Test
    fun `patchEntry sends only the set fields`() =
        runBlocking {
            repeat(3) { server.enqueueJson(DETAIL_JSON) }
            api.patchEntry(ENTRY_ID, EntryPatch(title = EntryPatch.Field.Set(null)))
            api.patchEntry(
                ENTRY_ID,
                EntryPatch(
                    title = EntryPatch.Field.Set("New"),
                    tags = EntryPatch.Field.Set(listOf("x")),
                    entryDate = EntryPatch.Field.Set("2026-11-09"),
                    text = EntryPatch.Field.Set("Body"),
                    expectedVersion = 3,
                ),
            )
            api.patchEntry(ENTRY_ID, EntryPatch(text = EntryPatch.Field.Set("Only text"), expectedVersion = 0))
            val first = takeRequest()
            assertEquals("PATCH", first.method)
            assertEquals("/api/v1/entries/$ENTRY_ID", first.url.encodedPath)
            assertEquals("""{"title":null}""", first.body?.utf8())
            assertEquals(
                """{"title":"New","tags":["x"],"entry_date":"2026-11-09","text":"Body","expected_version":3}""",
                takeRequest().body?.utf8(),
            )
            assertEquals("""{"text":"Only text","expected_version":0}""", takeRequest().body?.utf8())
        }

    @Test
    fun `facets decode`() =
        runBlocking {
            server.enqueueJson(
                """{"tags":[{"value":"school","count":4}],"projects":[],"skills":[{"value":"Rust","count":2}]}""",
            )
            val facets = api.facets()
            assertEquals(4, facets.tags.single().count)
            assertEquals("Rust", facets.skills.single().value)
        }

    // ------------------------------------------------------------------ precondition assertions

    @Test
    fun `programming errors trip assertions before any request`() {
        runBlocking {
            assertFailsWith<AssertionError> { api.listEntries(EntryFilters.NONE, SortOrder.NEWEST_FIRST, 0, 0) }
            assertFailsWith<AssertionError> { api.listEntries(EntryFilters.NONE, SortOrder.NEWEST_FIRST, 501, 0) }
            assertFailsWith<AssertionError> { api.listEntries(EntryFilters.NONE, SortOrder.NEWEST_FIRST, 10, -1) }
            assertFailsWith<AssertionError> { api.patchEntry(ENTRY_ID, EntryPatch(expectedVersion = 1)) }
            assertFailsWith<AssertionError> { api.createTextEntry(NewTextEntry("  ", null, emptyList(), "2026-11-11")) }
            val file = File.createTempFile("rec", ".ogg")
            try {
                assertFailsWith<AssertionError> {
                    api.createAudioEntry(
                        NewAudioEntry(file, "audio/ogg; codecs=opus", "r.ogg", null, emptyList(), "2026-11-11"),
                    )
                }
            } finally {
                file.delete()
            }
            assertFailsWith<AssertionError> { api.search("  ", EntryFilters.NONE, listOf(1)) }
            assertFailsWith<AssertionError> { api.search("q", EntryFilters.NONE, emptyList()) }
            assertFailsWith<AssertionError> { api.search("q", EntryFilters.NONE, listOf(1, 1)) }
            assertFailsWith<AssertionError> { api.search("q", EntryFilters.NONE, listOf(0)) }
            assertFailsWith<AssertionError> { api.search("q", EntryFilters.NONE, listOf(6)) }
            assertEquals(0, server.requestCount)
        }
    }
}
