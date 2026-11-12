package dev.msj.journal.core.api

import kotlinx.coroutines.flow.Flow

/**
 * The journal API v1 as the app uses it, with bearer-token authentication.
 * Every failure is an [ApiException] whose [ApiError] has a stable code;
 * coroutine cancellation propagates as [kotlinx.coroutines.CancellationException]
 * and cancels the underlying HTTP call.
 */
public interface JournalApi {
    /**
     * `GET /health` without credentials. Succeeds only when the body is
     * `{"status":"ok"}`; any other answer is NOT_A_JOURNAL_SERVER (wrong address,
     * a different service, or a proxy page), with the status and a bounded
     * body excerpt in the details.
     */
    public suspend fun health()

    /**
     * `GET /session` with the bearer token: succeeds on 204. A rejected token is
     * the server's 401 code (UNAUTHORIZED or TOKEN_REISSUE_REQUIRED).
     */
    public suspend fun checkSession()

    public suspend fun listEntries(
        filters: EntryFilters,
        order: SortOrder,
        limit: Int,
        offset: Int,
    ): EntryPage

    /** `GET /entries?view=full`: entries with their artifacts, for upload reconciliation. */
    public suspend fun listEntriesFull(
        filters: EntryFilters,
        limit: Int,
        offset: Int,
    ): FullEntryPage

    public suspend fun getEntry(id: String): EntryDetail

    /** `GET /entries/{id}/neighbors` within the filtered ordering (newest first). */
    public suspend fun neighbors(
        id: String,
        filters: EntryFilters,
    ): Neighbors

    public suspend fun createTextEntry(entry: NewTextEntry): EntryDetail

    /** Multipart upload streamed from the file; the field is `file`, with title, tags (JSON) and entry_date. */
    public suspend fun createAudioEntry(entry: NewAudioEntry): EntryDetail

    public suspend fun patchEntry(
        id: String,
        patch: EntryPatch,
    ): EntryDetail

    public suspend fun facets(): Facets

    /**
     * Streams `GET /search` (NDJSON). Each tier is emitted as soon as its line
     * arrives; the flow completes after the completion line. A stream that ends
     * without one fails with STREAM_INCOMPLETE; a broken connection with
     * STREAM_INTERRUPTED; an unparseable line with RESPONSE_INVALID.
     */
    public fun search(
        query: String,
        filters: EntryFilters,
        tiers: List<Int>,
    ): Flow<SearchEvent>

    public suspend fun queue(): QueueStatus

    /** `POST /batches/run` (202). An active batch is 409 BATCH_ALREADY_RUNNING. */
    public suspend fun runBatch(): BatchRequested
}
