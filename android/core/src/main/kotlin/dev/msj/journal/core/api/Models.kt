package dev.msj.journal.core.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * Response and request shapes of the journal API v1 (docs/api-v1-contract.md),
 * mirroring web/src/api/types.ts. Responses are decoded strictly for the fields
 * the app relies on and leniently for unknown extra fields, so a new server
 * field never breaks the app while a missing or mistyped one surfaces as
 * RESPONSE_INVALID naming the field.
 */

@Serializable
public enum class EntryStatus {
    @SerialName("queued")
    QUEUED,

    @SerialName("processing")
    PROCESSING,

    @SerialName("done")
    DONE,

    @SerialName("needs_attention")
    NEEDS_ATTENTION,

    @SerialName("failed")
    FAILED,
}

@Serializable
public enum class EntrySource {
    @SerialName("text")
    TEXT,

    @SerialName("audio")
    AUDIO,
}

/** One row of `GET /entries` (summary view); also embedded in search hits. */
@Serializable
public data class EntrySummary(
    val id: String,
    /** Journal date, `YYYY-MM-DD`. */
    @SerialName("entry_date") val entryDate: String,
    /** ISO 8601 timestamp. */
    @SerialName("created_at") val createdAt: String,
    val title: String?,
    val tags: List<String>,
    val source: EntrySource,
    /** Final summary text, if any. */
    val summary: String?,
    /** Final cleaned text when available, else the raw text. */
    val text: String,
    val projects: List<String>,
    val skills: List<String>,
    val status: EntryStatus,
    val revision: Int,
    /** Optimistic-concurrency version; null when the server does not report it. */
    val version: Int? = null,
)

@Serializable
public data class EntryPage(
    val items: List<EntrySummary>,
    val total: Int,
)

/** Artifact kinds the app reads; the field stays a string so a new server kind is not rejected. */
public object ArtifactKind {
    public const val RAW_AUDIO: String = "raw_audio"
    public const val RAW_TRANSCRIPT: String = "raw_transcript"
    public const val USER_TEXT: String = "user_text"
    public const val CLEANED: String = "cleaned"
    public const val SUMMARY: String = "summary"
    public const val EXTRACTION: String = "extraction"
    public const val REVIEW: String = "review"
}

@Serializable
public data class Artifact(
    val id: String,
    val kind: String,
    val content: String,
    val model: String? = null,
    @SerialName("created_at") val createdAt: String,
    val revision: Int,
    @SerialName("needs_attention") val needsAttention: Boolean = false,
    val target: String? = null,
    val turn: Int? = null,
    @SerialName("content_type") val contentType: String? = null,
    @SerialName("superseded_by") val supersededBy: String? = null,
)

@Serializable
public data class BlockedBy(
    @SerialName("job_id") val jobId: String,
    val kind: String,
    val message: String,
)

@Serializable
public data class Job(
    val id: String,
    val kind: String,
    /** queued, running, done, failed or superseded. */
    val status: String,
    val attempts: Int = 0,
    @SerialName("last_error") val lastError: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("finished_at") val finishedAt: String? = null,
    val revision: Int? = null,
    val target: String? = null,
    val turn: Int? = null,
    @SerialName("blocked_by") val blockedBy: List<BlockedBy> = emptyList(),
)

/**
 * `GET /entries/{id}`, and each item of `GET /entries?view=full`: the stored
 * entry plus the summary-view fields.
 */
@Serializable
public data class EntryDetail(
    val id: String,
    @SerialName("entry_date") val entryDate: String,
    @SerialName("created_at") val createdAt: String,
    val title: String?,
    val tags: List<String>,
    val source: EntrySource,
    val summary: String? = null,
    val text: String = "",
    val projects: List<String> = emptyList(),
    val skills: List<String> = emptyList(),
    val status: EntryStatus = EntryStatus.DONE,
    val revision: Int,
    val version: Int? = null,
    @SerialName("needs_attention") val needsAttention: Boolean = false,
    @SerialName("compilation_count") val compilationCount: Int = 0,
    val artifacts: List<Artifact> = emptyList(),
    val jobs: List<Job> = emptyList(),
) {
    /** The summary-view projection, for lists and navigation previews. */
    public fun toSummary(): EntrySummary =
        EntrySummary(
            id = id,
            entryDate = entryDate,
            createdAt = createdAt,
            title = title,
            tags = tags,
            source = source,
            summary = summary,
            text = text,
            projects = projects,
            skills = skills,
            status = status,
            revision = revision,
            version = version,
        )

    /**
     * The raw text of the current revision: the transcript for audio entries,
     * the typed text for text entries; null while an audio entry is untranscribed.
     */
    public fun rawText(): String? =
        artifacts
            .lastOrNull {
                it.revision == revision &&
                    it.supersededBy == null &&
                    (it.kind == ArtifactKind.RAW_TRANSCRIPT || it.kind == ArtifactKind.USER_TEXT)
            }?.content
}

/** `GET /entries?view=full`. */
@Serializable
public data class FullEntryPage(
    val items: List<EntryDetail>,
    val total: Int,
)

@Serializable
public data class Neighbors(
    val newer: String?,
    val older: String?,
)

@Serializable
public data class FacetValue(
    val value: String,
    val count: Int,
)

@Serializable
public data class Facets(
    val tags: List<FacetValue>,
    val projects: List<FacetValue>,
    val skills: List<FacetValue>,
)

/** Filter parameters shared by entries, neighbors and search. Dates are inclusive `YYYY-MM-DD`. */
public data class EntryFilters(
    val from: String? = null,
    val to: String? = null,
    val tags: List<String> = emptyList(),
    val projects: List<String> = emptyList(),
    val skills: List<String> = emptyList(),
) {
    public val hasFacets: Boolean get() = tags.isNotEmpty() || projects.isNotEmpty() || skills.isNotEmpty()

    public companion object {
        public val NONE: EntryFilters = EntryFilters()
    }
}

public enum class SortOrder(public val wire: String) {
    NEWEST_FIRST("desc"),
    OLDEST_FIRST("asc"),
}

@Serializable
public data class Unavailable(
    val code: String,
    val detail: String,
)

@Serializable
public data class SearchHit(
    @SerialName("entry_id") val entryId: String,
    val score: Double,
    val snippet: String? = null,
    /** Tier 5 only: the best matching chunk. */
    val passage: String? = null,
    /** The summary item, embedded by the server so a card needs no extra fetch. */
    val entry: EntrySummary? = null,
)

/** One NDJSON line of `GET /search`. */
public sealed interface SearchEvent {
    public data class Tier(
        val tier: Int,
        val name: String,
        val results: List<SearchHit>,
        val elapsedMs: Double,
        val unavailable: Unavailable?,
    ) : SearchEvent

    /** The completion line; [error] is set when the server failed mid-stream. */
    public data class Done(
        val elapsedMs: Double?,
        val error: StreamError?,
    ) : SearchEvent

    public data class StreamError(
        val status: Int?,
        val code: String?,
        val detail: String,
    )
}

@Serializable
public data class BatchResult(
    val processed: Int,
    val failed: Int,
)

@Serializable
public data class CodedDetail(
    val code: String,
    val detail: String,
)

@Serializable
public data class BatchState(
    val running: Boolean,
    @SerialName("started_at") val startedAt: String? = null,
    val trigger: String? = null,
    @SerialName("last_finished_at") val lastFinishedAt: String? = null,
    @SerialName("last_result") val lastResult: BatchResult? = null,
    @SerialName("last_error") val lastError: CodedDetail? = null,
    @SerialName("request_pending") val requestPending: Boolean = false,
)

@Serializable
public data class WorkerState(
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    /** Server-computed staleness; null when the server does not report it. */
    val stale: Boolean? = null,
    @SerialName("interval_s") val intervalS: Double? = null,
    @SerialName("next_run_at") val nextRunAt: String? = null,
    @SerialName("next_batch_at") val nextBatchAt: String? = null,
)

@Serializable
public data class QueueCounts(
    val queued: Int,
    val running: Int,
    val failed: Int,
    val blocked: Int,
)

@Serializable
public data class QueueItem(
    @SerialName("entry_id") val entryId: String? = null,
    @SerialName("entry_title") val entryTitle: String? = null,
    @SerialName("entry_date") val entryDate: String? = null,
    @SerialName("job_id") val jobId: String,
    val kind: String,
    val target: String? = null,
    val turn: Int? = null,
    /** queued, running or failed. */
    val status: String,
    @SerialName("blocked_by") val blockedBy: List<BlockedBy> = emptyList(),
    @SerialName("last_error") val lastError: String? = null,
    @SerialName("not_before") val notBefore: String? = null,
)

@Serializable
public data class QueueSnapshot(
    val batch: BatchState,
    val worker: WorkerState,
    val counts: QueueCounts,
    val items: List<QueueItem> = emptyList(),
)

/** `GET /queue` plus the server clock from the `Date` header (null when absent). */
public data class QueueStatus(
    val snapshot: QueueSnapshot,
    val serverTimeMs: Long?,
)

@Serializable
public data class StaleFlag(
    val stale: Boolean,
)

/** `POST /batches/run` (202). [worker] is set when no worker heartbeat is recent. */
@Serializable
public data class BatchRequested(
    val requested: Boolean,
    val worker: StaleFlag? = null,
)

public data class NewTextEntry(
    val text: String,
    val title: String?,
    val tags: List<String>,
    /** Always sent, so an entry written offline keeps the day it was written. */
    val entryDate: String,
)

public data class NewAudioEntry(
    val file: java.io.File,
    /** Bare MIME type accepted by the server (`_AUDIO_TYPES`), for example `audio/ogg`. */
    val mimeType: String,
    val fileName: String,
    val title: String?,
    val tags: List<String>,
    val entryDate: String,
)

/** `PATCH /entries/{id}`: only the fields that are set are sent. */
public data class EntryPatch(
    val title: Field<String?> = Field.Unset,
    val tags: Field<List<String>> = Field.Unset,
    val entryDate: Field<String> = Field.Unset,
    val text: Field<String> = Field.Unset,
    /** The version the edit started from; a mismatch is answered with 409 ENTRY_CHANGED. */
    val expectedVersion: Int? = null,
) {
    public val isEmpty: Boolean
        get() = title is Field.Unset && tags is Field.Unset && entryDate is Field.Unset && text is Field.Unset

    public sealed interface Field<out T> {
        public data object Unset : Field<Nothing>

        public data class Set<T>(
            val value: T,
        ) : Field<T>
    }
}
