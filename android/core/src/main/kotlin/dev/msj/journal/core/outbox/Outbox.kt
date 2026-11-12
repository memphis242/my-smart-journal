package dev.msj.journal.core.outbox

import dev.msj.journal.core.api.Delivery
import dev.msj.journal.core.api.DetailLine

/*
 * The durable outbox: every new entry is saved on the phone first and uploaded
 * later, surviving app kill, reboot and a disconnected VPN.
 *
 * `POST /entries` has no idempotency key (API gap), so the client prevents
 * duplicates itself:
 * - one record per capture, keyed by a client UUID made when the composer or
 *   recorder opens, so a double tap inserts nothing new;
 * - one uploader at a time (a unique WorkManager chain);
 * - a failure that may have reached the server (Delivery.MAYBE_SENT), or an
 *   attempt interrupted by process death, moves the record to UNCERTAIN, and an
 *   UNCERTAIN record is reconciled against the server's entries for its date
 *   before it is ever sent again.
 *
 * State machine (pure; see OutboxMachine):
 *
 *   PENDING --claim--> UPLOADING --created--> SENT
 *                         |  failed NOT_SENT / retryable RECEIVED --> RETRY_WAIT --due--> (claimable)
 *                         |  failed MAYBE_SENT / interrupted -------> UNCERTAIN --reconcile--> SENT | (claimable)
 *                         |  failed permanent (4xx) ----------------> FAILED --user retry--> PENDING
 *   any state except UPLOADING --user discard--> (deleted)
 */

public enum class OutboxState {
    /** Saved and due for its first upload. */
    PENDING,

    /** An upload attempt is in flight (claimed by the uploader). */
    UPLOADING,

    /** The last attempt failed without reaching the server, or was rejected in a retryable way; due at nextAttemptAtMs. */
    RETRY_WAIT,

    /** The last attempt may have created the entry; reconcile with the server before sending again. */
    UNCERTAIN,

    /** Rejected by the server in a way a retry cannot fix (validation, size, type); needs the user. */
    FAILED,

    /** Created on the server as [OutboxRecord.serverEntryId]; kept briefly so reconciliation can exclude it. */
    SENT,
}

public sealed interface OutboxPayload {
    public data class Text(
        val text: String,
    ) : OutboxPayload

    public data class Audio(
        /** Absolute path of the recording in the app's private outbox directory. */
        val path: String,
        /** Bare MIME type the server accepts: `audio/ogg` (Opus) or `audio/mp4` (AAC). */
        val mimeType: String,
        val fileName: String,
        /** Lowercase hex SHA-256 of the file; the server names the stored file after it. */
        val sha256: String,
        val sizeBytes: Long,
        val durationMs: Long,
    ) : OutboxPayload
}

/** The last failure, kept for display (code first, details one step away). */
public data class OutboxError(
    val code: String,
    val message: String,
    val hint: String?,
    val details: List<DetailLine>,
    val status: Int?,
    val delivery: Delivery,
    val atMs: Long,
)

public data class OutboxRecord(
    /** Client UUID, created when the capture started. */
    val id: String,
    val createdAtMs: Long,
    /** `YYYY-MM-DD`, the local day the entry was captured; always sent to the server. */
    val entryDate: String,
    val title: String?,
    val tags: List<String>,
    val payload: OutboxPayload,
    val state: OutboxState,
    /** Upload attempts started so far. */
    val attempts: Int,
    /** When the next attempt (or reconciliation) is due; 0 means now. */
    val nextAttemptAtMs: Long,
    /** When a request for this record first may have reached the server; bounds reconciliation. */
    val firstSentAtMs: Long?,
    val lastError: OutboxError?,
    val serverEntryId: String?,
    val updatedAtMs: Long,
) {
    init {
        assert(id.isNotBlank()) { "outbox record has an id" }
        assert(attempts >= 0) { "attempts are non-negative" }
        assert((state == OutboxState.SENT) == (serverEntryId != null)) { "only a sent record has a server entry id" }
        assert(title == null || title.isNotBlank()) { "a blank title is stored as null" }
    }
}

/** What happened to a record; [OutboxMachine.transition] maps (record, event) to the next record. */
public sealed interface OutboxEvent {
    /** The uploader starts an attempt. Allowed from PENDING, RETRY_WAIT and UNCERTAIN (after a reconcile miss). */
    public data object Claim : OutboxEvent

    /** `POST /entries` returned 201. */
    public data class Created(
        val entryId: String,
    ) : OutboxEvent

    /** The attempt failed with this error. */
    public data class Failed(
        val error: dev.msj.journal.core.api.ApiError,
    ) : OutboxEvent

    /** Reconciliation found the entry on the server. Allowed from UNCERTAIN. */
    public data class Reconciled(
        val entryId: String,
    ) : OutboxEvent

    /** Reconciliation itself failed (the server could not be asked); stays UNCERTAIN with backoff. */
    public data class ReconcileFailed(
        val error: dev.msj.journal.core.api.ApiError,
    ) : OutboxEvent

    /** The attempt was cut short (process death, cancellation) with the outcome unknown. Allowed from UPLOADING. */
    public data object Interrupted : OutboxEvent

    /** The user asked to try again now. Allowed from FAILED, RETRY_WAIT, UNCERTAIN and PENDING. */
    public data object UserRetry : OutboxEvent
}

/** How a failed attempt is handled. */
public enum class FailureClass {
    /** Did not reach the server, or the server rejected it before acting (5xx problem, 429): retry with backoff. */
    RETRY,

    /** May have created the entry: reconcile before any new attempt. */
    RECONCILE,

    /** The token was rejected (401): retry with backoff; signing in again makes everything due at once. */
    AUTH,

    /** A retry cannot succeed unchanged (400, 404, 413, 415, 422 and other 4xx): needs the user. */
    PERMANENT,
}
