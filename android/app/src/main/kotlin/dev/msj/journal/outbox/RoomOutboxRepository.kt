package dev.msj.journal.outbox

import android.util.Log
import dev.msj.journal.core.outbox.OutboxEvent
import dev.msj.journal.core.outbox.OutboxPayload
import dev.msj.journal.core.outbox.OutboxRecord
import dev.msj.journal.core.outbox.OutboxState
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.time.LocalDate
import java.time.format.DateTimeParseException

/**
 * [OutboxRepository] on the Room outbox table and the outbox directory.
 * Saves commit the record only once its recording is in place, and the
 * uploader is started after every change that makes something due.
 */
class RoomOutboxRepository(
    database: OutboxDatabase,
    private val files: OutboxFiles,
    private val scheduler: OutboxScheduler,
    private val clock: () -> Long,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : OutboxRepository {
    private val store = OutboxStore(database)

    override val unsent: Flow<List<OutboxRecord>> = store.unsent

    override suspend fun enqueueText(
        id: String,
        text: String,
        title: String?,
        tags: List<String>,
        entryDate: String,
    ): Boolean {
        validateCapture(id, entryDate)
        require(text.isNotBlank()) { "Save entry: the text is blank (record $id)" }
        val inserted = store.insert(newRecord(id, title, tags, entryDate, OutboxPayload.Text(text)))
        if (inserted) scheduler.kick()
        return inserted
    }

    override suspend fun enqueueAudio(
        id: String,
        recording: File,
        mimeType: String,
        durationMs: Long,
        title: String?,
        tags: List<String>,
        entryDate: String,
    ): Boolean {
        val extension =
            requireNotNull(AUDIO_EXTENSIONS[mimeType]) {
                "Save recording: unsupported audio type '$mimeType' (supported: ${AUDIO_EXTENSIONS.keys.joinToString()})"
            }
        validateCapture(id, entryDate)
        require(durationMs >= 0) { "Save recording: negative duration $durationMs ms (record $id)" }
        // Two saves of one id cannot both move a file onto `<id>.<ext>`, and the orphan sweep waits for the commit.
        val inserted =
            files.saveLock.withLock {
                // A saved id keeps its file; the caller's recording is left untouched.
                if (store.get(id) != null) return@withLock false
                val adopted = withContext(ioDispatcher) { files.adopt(recording, id, extension) }
                val stored =
                    try {
                        val record =
                            withContext(ioDispatcher) {
                                val sizeBytes = adopted.length()
                                if (sizeBytes <= 0L) {
                                    throw IOException(
                                        "Save recording failed: the recording ${recording.path} is empty.",
                                    )
                                }
                                val audio =
                                    OutboxPayload.Audio(
                                        path = adopted.path,
                                        mimeType = mimeType,
                                        fileName = adopted.name,
                                        sha256 = files.sha256(adopted),
                                        sizeBytes = sizeBytes,
                                        durationMs = durationMs,
                                    )
                                newRecord(id, title, tags, entryDate, audio)
                            }
                        store.insert(record)
                    } catch (error: Throwable) {
                        withContext(NonCancellable) { returnUncommitted(id, adopted, recording, error) }
                        throw error
                    }
                if (!stored) {
                    val existing = store.get(id)?.payload as? OutboxPayload.Audio
                    if (existing?.path != adopted.path) deleteFileOf(adopted.path)
                }
                stored
            }
        if (inserted) scheduler.kick()
        return inserted
    }

    override suspend fun retry(id: String) {
        val retried = store.transition(id, OutboxEvent.UserRetry, clock(), 0.0) { it in RETRYABLE }
        if (retried != null) scheduler.kick()
    }

    override suspend fun discard(id: String): Boolean =
        when (val result = store.deleteUnlessUploading(id)) {
            OutboxStore.DeleteResult.Missing -> {
                true
            }

            is OutboxStore.DeleteResult.Refused -> {
                false
            }

            is OutboxStore.DeleteResult.Deleted -> {
                (result.record.payload as? OutboxPayload.Audio)?.let { deleteFileOf(it.path) }
                true
            }
        }

    override suspend fun retryAllNow() {
        store.makeWaitingDue(clock())
        scheduler.kick()
    }

    override fun kick() {
        scheduler.kick()
    }

    override suspend fun wipe() {
        scheduler.cancelAll()
        val rows = store.deleteAll()
        val emptied = withContext(ioDispatcher) { files.wipe() }
        if (!emptied) {
            throw IOException("Sign-out: deleted $rows outbox record(s) but could not delete every saved recording.")
        }
    }

    private fun validateCapture(
        id: String,
        entryDate: String,
    ) {
        require(id.isNotBlank()) { "Save entry: the record id is blank" }
        try {
            LocalDate.parse(entryDate)
        } catch (error: DateTimeParseException) {
            throw IllegalArgumentException("Save entry: entry date '$entryDate' is not YYYY-MM-DD (record $id)", error)
        }
    }

    private fun newRecord(
        id: String,
        title: String?,
        tags: List<String>,
        entryDate: String,
        payload: OutboxPayload,
    ): OutboxRecord {
        val nowMs = clock()
        return OutboxRecord(
            id = id,
            createdAtMs = nowMs,
            entryDate = entryDate,
            title = title?.takeIf { it.isNotBlank() },
            tags = tags,
            payload = payload,
            state = OutboxState.PENDING,
            attempts = 0,
            nextAttemptAtMs = 0L,
            firstSentAtMs = null,
            lastError = null,
            serverEntryId = null,
            updatedAtMs = nowMs,
        )
    }

    /**
     * After a failed save, moves [adopted] back to [original] so the caller
     * still has the recording, unless the record was committed after all
     * (a cancellation that arrived after the insert). Problems are attached
     * to [error] rather than replacing it.
     */
    private suspend fun returnUncommitted(
        id: String,
        adopted: File,
        original: File,
        error: Throwable,
    ) {
        val committedPath =
            try {
                (store.get(id)?.payload as? OutboxPayload.Audio)?.path
            } catch (readError: Exception) {
                error.addSuppressed(readError)
                // Unknown whether the record exists, so the file stays where a record may point.
                return
            }
        if (committedPath == adopted.path) return
        try {
            withContext(ioDispatcher) { files.restore(adopted, original) }
        } catch (restoreError: IOException) {
            error.addSuppressed(restoreError)
        }
    }

    private suspend fun deleteFileOf(path: String) {
        val deleted = withContext(ioDispatcher) { files.delete(path) }
        if (!deleted) Log.w(TAG, "Could not delete the outbox recording $path; the orphan sweep retries it")
    }

    private companion object {
        const val TAG = "MsjOutbox"

        val RETRYABLE: Set<OutboxState> =
            setOf(OutboxState.FAILED, OutboxState.RETRY_WAIT, OutboxState.UNCERTAIN, OutboxState.PENDING)

        /** Bare MIME types the server accepts (`_AUDIO_TYPES` in backend/src/msj/api.py) and the file extension used here. */
        val AUDIO_EXTENSIONS: Map<String, String> =
            mapOf(
                "audio/ogg" to "ogg",
                "audio/mp4" to "m4a",
                "audio/mpeg" to "mp3",
                "audio/wav" to "wav",
                "audio/x-wav" to "wav",
                "audio/webm" to "webm",
                "audio/flac" to "flac",
                "audio/x-flac" to "flac",
            )
    }
}
