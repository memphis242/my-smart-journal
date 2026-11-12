package dev.msj.journal.outbox

import dev.msj.journal.core.api.ApiError
import dev.msj.journal.core.api.ApiException
import dev.msj.journal.core.api.Artifact
import dev.msj.journal.core.api.ArtifactKind
import dev.msj.journal.core.api.BatchRequested
import dev.msj.journal.core.api.Delivery
import dev.msj.journal.core.api.DetailLine
import dev.msj.journal.core.api.EntryDetail
import dev.msj.journal.core.api.EntryFilters
import dev.msj.journal.core.api.EntryPage
import dev.msj.journal.core.api.EntryPatch
import dev.msj.journal.core.api.EntrySource
import dev.msj.journal.core.api.ErrorKind
import dev.msj.journal.core.api.Facets
import dev.msj.journal.core.api.FullEntryPage
import dev.msj.journal.core.api.JournalApi
import dev.msj.journal.core.api.Neighbors
import dev.msj.journal.core.api.NewAudioEntry
import dev.msj.journal.core.api.NewTextEntry
import dev.msj.journal.core.api.QueueStatus
import dev.msj.journal.core.api.SearchEvent
import dev.msj.journal.core.api.SortOrder
import kotlinx.coroutines.flow.Flow
import java.time.Instant

internal fun httpError(
    status: Int,
    code: String,
    details: List<DetailLine> = emptyList(),
): ApiError =
    ApiError(
        kind = ErrorKind.HTTP,
        code = code,
        message = "Create entry failed: HTTP $status $code.",
        hint = null,
        details = details,
        status = status,
        method = "POST",
        path = "/entries",
        retryAfterS = null,
        delivery = Delivery.RECEIVED,
    )

internal fun networkError(
    delivery: Delivery,
    code: String,
): ApiError =
    ApiError(
        kind = ErrorKind.NETWORK,
        code = code,
        message = "Create entry failed: $code.",
        hint = "Connect to the VPN.",
        details = listOf(DetailLine("Cause", code)),
        status = null,
        method = "POST",
        path = "/entries",
        retryAfterS = null,
        delivery = delivery,
    )

/**
 * A journal server in memory. [beforeCreate] runs before each POST is
 * applied; it may throw an [ApiException] (the request failed) or set
 * [createThenFail] so the entry is stored and the call still fails (the
 * response was lost).
 */
internal class FakeJournalApi(
    private val clock: () -> Long,
) : JournalApi {
    val entries: MutableList<EntryDetail> = mutableListOf()
    val createCalls: MutableList<Any> = mutableListOf()
    var listCalls: Int = 0
    var beforeCreate: suspend (Any) -> Unit = {}
    var createThenFail: ApiError? = null
    var listFailure: ApiError? = null

    override suspend fun createTextEntry(entry: NewTextEntry): EntryDetail {
        createCalls += entry
        beforeCreate(entry)
        return store(entry.entryDate, entry.title, entry.tags, EntrySource.TEXT, ArtifactKind.USER_TEXT, entry.text)
    }

    override suspend fun createAudioEntry(entry: NewAudioEntry): EntryDetail {
        createCalls += entry
        beforeCreate(entry)
        val sha = OutboxFiles(entry.file.parentFile!!).sha256(entry.file)
        val id = "srv-${entries.size + 1}"
        val ext = entry.fileName.substringAfterLast('.')
        return store(
            entry.entryDate,
            entry.title,
            entry.tags,
            EntrySource.AUDIO,
            ArtifactKind.RAW_AUDIO,
            "audio/$id/$sha.$ext",
        )
    }

    private fun store(
        entryDate: String,
        title: String?,
        tags: List<String>,
        source: EntrySource,
        kind: String,
        content: String,
    ): EntryDetail {
        val id = "srv-${entries.size + 1}"
        val createdAt = Instant.ofEpochMilli(clock()).toString()
        val detail =
            EntryDetail(
                id = id,
                entryDate = entryDate,
                createdAt = createdAt,
                title = title,
                tags = tags,
                source = source,
                revision = 1,
                artifacts =
                    listOf(
                        Artifact(id = "a-$id", kind = kind, content = content, createdAt = createdAt, revision = 1),
                    ),
            )
        entries += detail
        createThenFail?.let { error ->
            createThenFail = null
            throw ApiException(error)
        }
        return detail
    }

    override suspend fun listEntriesFull(
        filters: EntryFilters,
        limit: Int,
        offset: Int,
    ): FullEntryPage {
        listCalls++
        listFailure?.let { throw ApiException(it) }
        val matching = entries.filter { it.entryDate == filters.from && it.entryDate == filters.to }
        return FullEntryPage(matching.drop(offset).take(limit), matching.size)
    }

    override suspend fun health(): Unit = unused()

    override suspend fun checkSession(): Unit = unused()

    override suspend fun listEntries(
        filters: EntryFilters,
        order: SortOrder,
        limit: Int,
        offset: Int,
    ): EntryPage = unused()

    override suspend fun getEntry(id: String): EntryDetail = unused()

    override suspend fun neighbors(
        id: String,
        filters: EntryFilters,
    ): Neighbors = unused()

    override suspend fun patchEntry(
        id: String,
        patch: EntryPatch,
    ): EntryDetail = unused()

    override suspend fun facets(): Facets = unused()

    override fun search(
        query: String,
        filters: EntryFilters,
        tiers: List<Int>,
    ): Flow<SearchEvent> = unused()

    override suspend fun queue(): QueueStatus = unused()

    override suspend fun runBatch(): BatchRequested = unused()

    private fun unused(): Nothing = throw UnsupportedOperationException("not used by the outbox")
}

internal class FakeScheduler : OutboxScheduler {
    var kicks: Int = 0
    var uploadsEnqueued: Int = 0
    var wakeupAtMs: Long? = null
    var cancelled: Int = 0

    override fun kick() {
        kicks++
    }

    override suspend fun enqueueUpload() {
        uploadsEnqueued++
    }

    override suspend fun scheduleWakeup(atMs: Long?) {
        wakeupAtMs = atMs
    }

    override suspend fun cancelAllWhile(block: suspend () -> Unit) {
        cancelled++
        block()
    }
}
