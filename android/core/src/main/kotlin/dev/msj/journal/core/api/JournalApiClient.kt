package dev.msj.journal.core.api

import dev.msj.journal.core.ndjson.NdjsonLineError
import dev.msj.journal.core.ndjson.NdjsonReader
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.channels.ProducerScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** The shared HTTP client configuration for [JournalApiClient]. */
public object JournalHttp {
    public fun defaultClient(): OkHttpClient =
        OkHttpClient
            .Builder()
            .connectTimeout(CONNECT_TIMEOUT_S, TimeUnit.SECONDS)
            .readTimeout(READ_TIMEOUT_S, TimeUnit.SECONDS)
            .writeTimeout(WRITE_TIMEOUT_S, TimeUnit.SECONDS)
            .build()

    private const val CONNECT_TIMEOUT_S = 15L
    private const val READ_TIMEOUT_S = 60L
    private const val WRITE_TIMEOUT_S = 120L
}

/**
 * [JournalApi] over OkHttp with a bearer token.
 *
 * [http] is used as given except that this class installs its own event
 * listener (to learn whether a request began being written, which decides
 * [Delivery]) and turns off OkHttp's silent connection-failure retry for
 * writes, so a POST or PATCH is never replayed behind the caller's back. The
 * token is sent only in the `Authorization` header and is removed from every
 * error this class produces.
 */
public class JournalApiClient(
    private val server: ServerUrl,
    private val token: String,
    http: OkHttpClient,
) : JournalApi {
    private val readClient: OkHttpClient = http.newBuilder().eventListenerFactory(ProbeListener.FACTORY).build()
    private val writeClient: OkHttpClient = readClient.newBuilder().retryOnConnectionFailure(false).build()

    // Tiers run model inference on the server, so a stream may stay silent longer than an ordinary read.
    private val streamClient: OkHttpClient =
        readClient
            .newBuilder()
            .apply { if (readClient.readTimeoutMillis in 1 until STREAM_READ_TIMEOUT_MS) readTimeout(STREAM_READ_TIMEOUT_MS, TimeUnit.MILLISECONDS) }
            .build()

    // ------------------------------------------------------------------ session

    override suspend fun health() {
        val op = OperationInfo("Check server", "GET", "/health")
        exchange(op, server.api(op.path), authenticated = false) { response ->
            val text = response.body.string()
            if (response.code != HTTP_OK || !isHealthy(text)) throw ApiException(notAJournalServer(op, response, text))
        }
    }

    override suspend fun checkSession() {
        val op = OperationInfo("Check sign-in", "GET", "/session")
        exchange(op, server.api(op.path)) { response ->
            requireSuccess(op, response)
            if (response.code != HTTP_NO_CONTENT) {
                throw ApiException(responseInvalid(op, DetailLine("Status", "${response.code} (expected 204)")))
            }
        }
    }

    // ------------------------------------------------------------------ entries

    override suspend fun listEntries(
        filters: EntryFilters,
        order: SortOrder,
        limit: Int,
        offset: Int,
    ): EntryPage {
        assert(limit in 1..MAX_LIMIT) { "limit is 1-$MAX_LIMIT: $limit" }
        assert(offset >= 0) { "offset is non-negative: $offset" }
        val op = OperationInfo("Load entries", "GET", "/entries")
        val url =
            server.api(op.path).withQuery {
                addFilters(filters)
                addQueryParameter("view", "summary")
                addQueryParameter("order", order.wire)
                addQueryParameter("limit", limit.toString())
                addQueryParameter("offset", offset.toString())
            }
        return getJson(op, url, EntryPage.serializer())
    }

    override suspend fun listEntriesFull(
        filters: EntryFilters,
        limit: Int,
        offset: Int,
    ): FullEntryPage {
        assert(limit in 1..MAX_LIMIT) { "limit is 1-$MAX_LIMIT: $limit" }
        assert(offset >= 0) { "offset is non-negative: $offset" }
        val op = OperationInfo("Load entries", "GET", "/entries")
        val url =
            server.api(op.path).withQuery {
                addFilters(filters)
                addQueryParameter("view", "full")
                addQueryParameter("limit", limit.toString())
                addQueryParameter("offset", offset.toString())
            }
        return getJson(op, url, FullEntryPage.serializer())
    }

    override suspend fun getEntry(id: String): EntryDetail {
        val op = OperationInfo("Load entry", "GET", "/entries/${segment(id)}")
        return getJson(op, server.api(op.path), EntryDetail.serializer())
    }

    override suspend fun neighbors(
        id: String,
        filters: EntryFilters,
    ): Neighbors {
        val op = OperationInfo("Find the next entry", "GET", "/entries/${segment(id)}/neighbors")
        return getJson(op, server.api(op.path).withQuery { addFilters(filters) }, Neighbors.serializer())
    }

    override suspend fun createTextEntry(entry: NewTextEntry): EntryDetail {
        assert(entry.text.isNotBlank()) { "entry text is non-blank" }
        val op = OperationInfo("Add entry", "POST", "/entries")
        val body =
            JsonObject(
                mapOf(
                    "text" to JsonPrimitive(entry.text),
                    "title" to (entry.title?.let(::JsonPrimitive) ?: JsonNull),
                    "tags" to JsonArray(entry.tags.map(::JsonPrimitive)),
                    "entry_date" to JsonPrimitive(entry.entryDate),
                ),
            )
        return sendJson(op, body.toRequestBody(), EntryDetail.serializer())
    }

    override suspend fun createAudioEntry(entry: NewAudioEntry): EntryDetail {
        assert(!entry.mimeType.contains(';')) { "audio MIME type is bare, without parameters: ${entry.mimeType}" }
        val op = OperationInfo("Upload recording", "POST", "/entries")
        if (!entry.file.isFile || !entry.file.canRead()) {
            throw ApiException(
                ApiError(
                    kind = ErrorKind.NETWORK,
                    code = ClientCodes.NETWORK_ERROR,
                    message = "${op.operation} failed: the recording file is missing or unreadable.",
                    hint = "Keep the recording and try again; if the file is gone, record it again.",
                    details = listOf(DetailLine("Request", op.requestLine), DetailLine("File", entry.file.path)),
                    status = null,
                    method = op.method,
                    path = op.path,
                    retryAfterS = null,
                    delivery = Delivery.NOT_SENT,
                ),
            )
        }
        val form =
            MultipartBody
                .Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("file", entry.fileName, entry.file.asRequestBody(entry.mimeType.toMediaType()))
                .apply { entry.title?.let { addFormDataPart("title", it) } }
                .addFormDataPart("entry_date", entry.entryDate)
                .addFormDataPart("tags", JsonArray(entry.tags.map(::JsonPrimitive)).toString())
                .build()
        return sendJson(op, form, EntryDetail.serializer(), callTimeoutMs = UPLOAD_CALL_TIMEOUT_MS)
    }

    override suspend fun patchEntry(
        id: String,
        patch: EntryPatch,
    ): EntryDetail {
        assert(!patch.isEmpty) { "a patch changes at least one field" }
        val op = OperationInfo("Save entry", "PATCH", "/entries/${segment(id)}")
        val fields = linkedMapOf<String, JsonElement>()
        (patch.title as? EntryPatch.Field.Set)?.let { fields["title"] = it.value?.let(::JsonPrimitive) ?: JsonNull }
        (patch.tags as? EntryPatch.Field.Set)?.let { fields["tags"] = JsonArray(it.value.map(::JsonPrimitive)) }
        (patch.entryDate as? EntryPatch.Field.Set)?.let { fields["entry_date"] = JsonPrimitive(it.value) }
        (patch.text as? EntryPatch.Field.Set)?.let { fields["text"] = JsonPrimitive(it.value) }
        patch.expectedVersion?.let { fields["expected_version"] = JsonPrimitive(it) }
        return sendJson(op, JsonObject(fields).toRequestBody(), EntryDetail.serializer())
    }

    override suspend fun facets(): Facets {
        val op = OperationInfo("Load filters", "GET", "/facets")
        return getJson(op, server.api(op.path), Facets.serializer())
    }

    // ------------------------------------------------------------------ search

    override fun search(
        query: String,
        filters: EntryFilters,
        tiers: List<Int>,
    ): Flow<SearchEvent> {
        assert(query.isNotBlank()) { "search query is non-blank" }
        assert(tiers.isNotEmpty() && tiers.all { it in 1..MAX_TIER } && tiers.toSet().size == tiers.size) {
            "tiers are unique and in 1..$MAX_TIER: $tiers"
        }
        val op = OperationInfo("Search", "GET", "/search")
        val url =
            server.api(op.path).withQuery {
                addQueryParameter("q", query)
                addFilters(filters)
                addQueryParameter("tiers", tiers.joinToString(","))
            }
        return channelFlow {
            val probe = CallProbe()
            val call = streamClient.newCall(request(op, url, probe, accept = NDJSON_TYPE))
            // Closing the call is the only way to interrupt a blocked socket read.
            val canceller =
                launch {
                    try {
                        awaitCancellation()
                    } finally {
                        call.cancel()
                    }
                }
            try {
                withContext(Dispatchers.IO) { streamSearch(op, call, probe) }
            } finally {
                canceller.cancel()
            }
        }
    }

    private suspend fun ProducerScope<SearchEvent>.streamSearch(
        op: OperationInfo,
        call: Call,
        probe: CallProbe,
    ) {
        val response =
            try {
                call.execute()
            } catch (e: IOException) {
                throw failure(transportError(op, server.host, probe.requestStarted, e), e)
            }
        response.use {
            if (!it.isSuccessful) throw failure(httpError(op, it))
            val reader = NdjsonReader(it.body.source())
            var sawDone = false
            while (!sawDone) {
                val element =
                    try {
                        reader.next()
                    } catch (e: NdjsonLineError) {
                        throw failure(streamLineInvalid(op, reader.lineNumber, e), e)
                    } catch (e: IOException) {
                        throw failure(streamInterrupted(op, it.code, e), e)
                    } ?: break
                val event =
                    try {
                        searchEvent(element)
                    } catch (e: SerializationException) {
                        throw failure(streamLineInvalid(op, reader.lineNumber, e), e)
                    } catch (e: IllegalArgumentException) {
                        throw failure(streamLineInvalid(op, reader.lineNumber, e), e)
                    }
                send(event)
                sawDone = event is SearchEvent.Done
            }
            if (!sawDone) throw failure(streamIncomplete(op, it.code))
        }
    }

    private fun streamLineInvalid(
        op: OperationInfo,
        line: Int,
        cause: Throwable,
    ): ApiError {
        val shown = if (cause is NdjsonLineError) cause.text else cause.message.orEmpty()
        return responseInvalid(
            op,
            DetailLine("Stream line", line.toString()),
            DetailLine("Cause", bounded(shown, DETAIL_CHARS)),
        )
    }

    private fun streamInterrupted(
        op: OperationInfo,
        status: Int,
        cause: IOException,
    ): ApiError =
        ApiError(
            kind = ErrorKind.NETWORK,
            code = ClientCodes.STREAM_INTERRUPTED,
            message = "${op.operation} failed: the result stream stopped before it finished.",
            hint = Hints.forCode(ClientCodes.STREAM_INTERRUPTED, null),
            details = listOf(DetailLine("Request", op.requestLine), DetailLine("Cause", causeChain(cause))),
            status = status,
            method = op.method,
            path = op.path,
            retryAfterS = null,
            delivery = Delivery.RECEIVED,
        )

    private fun streamIncomplete(
        op: OperationInfo,
        status: Int,
    ): ApiError =
        ApiError(
            kind = ErrorKind.RESPONSE,
            code = ClientCodes.STREAM_INCOMPLETE,
            message = "${op.operation} failed: the result stream ended without its completion line.",
            hint = Hints.forCode(ClientCodes.STREAM_INCOMPLETE, null),
            details = listOf(DetailLine("Request", op.requestLine)),
            status = status,
            method = op.method,
            path = op.path,
            retryAfterS = null,
            delivery = Delivery.RECEIVED,
        )

    // ------------------------------------------------------------------ queue

    override suspend fun queue(): QueueStatus {
        val op = OperationInfo("Load the queue", "GET", "/queue")
        return exchange(op, server.api(op.path)) { response ->
            requireSuccess(op, response)
            val serverTimeMs = response.headers.getInstant("Date")?.toEpochMilli()
            QueueStatus(decode(op, response.body.string(), QueueSnapshot.serializer()), serverTimeMs)
        }
    }

    override suspend fun runBatch(): BatchRequested {
        val op = OperationInfo("Run batch", "POST", "/batches/run")
        return sendJson(op, ByteArray(0).toRequestBody(), BatchRequested.serializer())
    }

    // ------------------------------------------------------------------ plumbing

    private suspend fun <T> getJson(
        op: OperationInfo,
        url: HttpUrl,
        serializer: KSerializer<T>,
    ): T =
        exchange(op, url) { response ->
            requireSuccess(op, response)
            decode(op, response.body.string(), serializer)
        }

    private suspend fun <T> sendJson(
        op: OperationInfo,
        body: RequestBody,
        serializer: KSerializer<T>,
        callTimeoutMs: Long = 0,
    ): T =
        exchange(op, server.api(op.path), body = body, callTimeoutMs = callTimeoutMs) { response ->
            requireSuccess(op, response)
            decode(op, response.body.string(), serializer)
        }

    private fun request(
        op: OperationInfo,
        url: HttpUrl,
        probe: CallProbe,
        body: RequestBody? = null,
        authenticated: Boolean = true,
        accept: String = JSON_TYPE,
    ): Request =
        Request
            .Builder()
            .url(url)
            .method(op.method, body)
            .header("Accept", accept)
            .apply { if (authenticated) header("Authorization", "Bearer $token") }
            .tag(CallProbe::class.java, probe)
            .build()

    /**
     * Sends the request and runs [handle] on the response (any status) while
     * its body is open. [handle] reads the body on OkHttp's thread, so a
     * cancelled coroutine unblocks it by cancelling the call.
     */
    private suspend fun <T> exchange(
        op: OperationInfo,
        url: HttpUrl,
        body: RequestBody? = null,
        authenticated: Boolean = true,
        callTimeoutMs: Long = 0,
        handle: (Response) -> T,
    ): T =
        suspendCancellableCoroutine { continuation ->
            val probe = CallProbe()
            val client = if (op.method == "GET") readClient else writeClient
            val call = client.newCall(request(op, url, probe, body, authenticated))
            if (callTimeoutMs > 0) call.timeout().timeout(callTimeoutMs, TimeUnit.MILLISECONDS)
            continuation.invokeOnCancellation { call.cancel() }
            call.enqueue(
                object : Callback {
                    override fun onFailure(
                        call: Call,
                        e: IOException,
                    ) {
                        continuation.resumeWithException(failure(transportError(op, server.host, probe.requestStarted, e), e))
                    }

                    override fun onResponse(
                        call: Call,
                        response: Response,
                    ) {
                        val outcome =
                            try {
                                Result.success(response.use(handle))
                            } catch (e: ApiException) {
                                Result.failure(failure(e.error, e.cause))
                            } catch (e: IOException) {
                                Result.failure(failure(transportError(op, server.host, probe.requestStarted, e), e))
                            } catch (e: Exception) {
                                Result.failure(e)
                            }
                        outcome.fold(continuation::resume, continuation::resumeWithException)
                    }
                },
            )
        }

    private fun requireSuccess(
        op: OperationInfo,
        response: Response,
    ) {
        if (!response.isSuccessful) throw failure(httpError(op, response))
    }

    private fun <T> decode(
        op: OperationInfo,
        text: String,
        serializer: KSerializer<T>,
    ): T =
        try {
            JSON.decodeFromString(serializer, text)
        } catch (e: SerializationException) {
            throw failure(responseInvalid(op, DetailLine("Cause", bounded(e.message.orEmpty(), DETAIL_CHARS))), e)
        } catch (e: IllegalArgumentException) {
            throw failure(responseInvalid(op, DetailLine("Cause", bounded(e.message.orEmpty(), DETAIL_CHARS))), e)
        }

    private fun notAJournalServer(
        op: OperationInfo,
        response: Response,
        body: String,
    ): ApiError {
        val details =
            mutableListOf(
                DetailLine("Request", op.requestLine),
                DetailLine("Status", "${response.code} ${response.message}".trim()),
            )
        if (body.isNotEmpty()) details += DetailLine("Body", bounded(body, BODY_EXCERPT_CHARS))
        return ApiError(
            kind = ErrorKind.RESPONSE,
            code = ClientCodes.NOT_A_JOURNAL_SERVER,
            message = "${op.operation} failed: $server answered, but not like a journal server (expected {\"status\":\"ok\"}).",
            hint = Hints.forCode(ClientCodes.NOT_A_JOURNAL_SERVER, null),
            details = details,
            status = response.code,
            method = op.method,
            path = op.path,
            retryAfterS = null,
            delivery = Delivery.RECEIVED,
        )
    }

    private fun isHealthy(text: String): Boolean =
        try {
            val status = (Json.parseToJsonElement(text) as? JsonObject)?.get("status") as? JsonPrimitive
            status != null && status.isString && status.content == "ok"
        } catch (_: IllegalArgumentException) {
            false
        }

    /** The exception for [error] with the token removed from every text field. */
    private fun failure(
        error: ApiError,
        cause: Throwable? = null,
    ): ApiException = ApiException(scrub(error), cause)

    private fun scrub(error: ApiError): ApiError {
        if (token.isEmpty()) return error
        fun String.clean(): String = replace(token, REDACTED)
        return error.copy(
            message = error.message.clean(),
            hint = error.hint?.clean(),
            details = error.details.map { DetailLine(it.label, it.value.clean()) },
        )
    }

    private fun HttpUrl.withQuery(block: HttpUrl.Builder.() -> Unit): HttpUrl = newBuilder().apply(block).build()

    private fun HttpUrl.Builder.addFilters(filters: EntryFilters) {
        filters.from?.let { addQueryParameter("from", it) }
        filters.to?.let { addQueryParameter("to", it) }
        filters.tags.forEach { addQueryParameter("tag", it) }
        filters.projects.forEach { addQueryParameter("project", it) }
        filters.skills.forEach { addQueryParameter("skill", it) }
    }

    private fun JsonElement.toRequestBody(): RequestBody = toString().toRequestBody(JSON_BODY_TYPE.toMediaType())

    // ------------------------------------------------------------------ search lines

    @Serializable
    private class TierLine(
        val tier: Int,
        val name: String,
        val results: List<SearchHit>,
        @SerialName("elapsed_ms") val elapsedMs: Double = 0.0,
        val unavailable: Unavailable? = null,
    )

    @Serializable
    private class DoneErrorLine(
        val status: Int? = null,
        val code: String? = null,
        val detail: String,
    )

    @Serializable
    private class DoneLine(
        @SerialName("elapsed_ms") val elapsedMs: Double? = null,
        val error: DoneErrorLine? = null,
    )

    private fun searchEvent(element: JsonElement): SearchEvent {
        val record = element as? JsonObject ?: throw SerializationException("expected an object, found $element")
        if ((record["done"] as? JsonPrimitive)?.booleanOrNull == true) {
            val line = JSON.decodeFromJsonElement(DoneLine.serializer(), record)
            val error = line.error?.let { SearchEvent.StreamError(it.status, it.code, it.detail) }
            return SearchEvent.Done(line.elapsedMs, error)
        }
        if ("tier" !in record) throw SerializationException("expected a tier or completion line, found keys ${record.keys}")
        val line = JSON.decodeFromJsonElement(TierLine.serializer(), record)
        if (line.tier !in 1..MAX_TIER) throw SerializationException("tier must be in 1..$MAX_TIER, found ${line.tier}")
        return SearchEvent.Tier(line.tier, line.name, line.results, line.elapsedMs, line.unavailable)
    }

    private companion object {
        val JSON: Json = Json { ignoreUnknownKeys = true }
        const val JSON_TYPE = "application/json"
        const val NDJSON_TYPE = "application/x-ndjson"
        const val JSON_BODY_TYPE = "application/json; charset=utf-8"
        const val REDACTED = "[token]"
        const val MAX_LIMIT = 500
        const val MAX_TIER = 5
        const val HTTP_OK = 200
        const val HTTP_NO_CONTENT = 204
        const val UPLOAD_CALL_TIMEOUT_MS = 10L * 60 * 1000
        const val STREAM_READ_TIMEOUT_MS = 3L * 60 * 1000
    }
}

/** A path segment for [id]: every byte outside the unreserved set is percent-encoded. */
private fun segment(id: String): String {
    assert(id.isNotEmpty() && id != "." && id != "..") { "entry id is a usable path segment: '$id'" }
    val out = StringBuilder()
    for (byte in id.toByteArray(Charsets.UTF_8)) {
        val c = byte.toInt() and 0xFF
        if (c.toChar() in 'A'..'Z' || c.toChar() in 'a'..'z' || c.toChar() in '0'..'9' || c.toChar() in "-._~") {
            out.append(c.toChar())
        } else {
            out.append('%').append("%02X".format(c))
        }
    }
    return out.toString()
}
