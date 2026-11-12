package dev.msj.journal.outbox

import dev.msj.journal.core.outbox.OutboxRecord
import kotlinx.coroutines.flow.Flow
import java.io.File

/**
 * The durable outbox as the UI sees it. Every write is a single database
 * transaction; uploads happen in the background (WorkManager) and are started
 * by [enqueueText], [enqueueAudio], [retry] and [kick].
 */
interface OutboxRepository {
    /** Every record that is not yet sent, oldest first. */
    val unsent: Flow<List<OutboxRecord>>

    /**
     * When a record last became SENT (uploaded or found on the server), or
     * null while none is kept. It changes on every completed upload, so a
     * screen can refresh from the server even when uploads finish too fast
     * for [unsent] to show the intermediate counts.
     */
    val lastSentAtMs: Flow<Long?>

    /**
     * Saves a text entry. [id] is the capture's client UUID: inserting the same
     * id twice is a no-op, so a repeated Save cannot create a second entry.
     * Returns true when a new record was inserted.
     */
    suspend fun enqueueText(
        id: String,
        text: String,
        title: String?,
        tags: List<String>,
        entryDate: String,
    ): Boolean

    /**
     * Saves a recording. [recording] is moved (or copied, then deleted) into the
     * outbox directory before the record is committed; its SHA-256 is computed
     * here. Same id semantics as [enqueueText].
     */
    suspend fun enqueueAudio(
        id: String,
        recording: File,
        mimeType: String,
        durationMs: Long,
        title: String?,
        tags: List<String>,
        entryDate: String,
    ): Boolean

    /** Makes the record due now (UserRetry) and starts the uploader. */
    suspend fun retry(id: String)

    /** Deletes the record and its recording. Refused (returns false) while an upload is in flight. */
    suspend fun discard(id: String): Boolean

    /** Makes every waiting record due now and starts the uploader (after sign-in, on reconnect). */
    suspend fun retryAllNow()

    /** Starts the uploader if anything is due, without changing any record. */
    fun kick()

    /** Deletes every record and recording and cancels background uploads (sign-out). */
    suspend fun wipe()
}
