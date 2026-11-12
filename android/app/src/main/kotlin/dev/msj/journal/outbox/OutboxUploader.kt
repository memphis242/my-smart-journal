package dev.msj.journal.outbox

import android.util.Log
import dev.msj.journal.core.api.ApiError
import dev.msj.journal.core.api.ApiException
import dev.msj.journal.core.api.Delivery
import dev.msj.journal.core.api.DetailLine
import dev.msj.journal.core.api.EntryDetail
import dev.msj.journal.core.api.EntryFilters
import dev.msj.journal.core.api.JournalApi
import dev.msj.journal.core.api.NewAudioEntry
import dev.msj.journal.core.api.NewTextEntry
import dev.msj.journal.core.outbox.FailureClass
import dev.msj.journal.core.outbox.OutboxCodes
import dev.msj.journal.core.outbox.OutboxEvent
import dev.msj.journal.core.outbox.OutboxMachine
import dev.msj.journal.core.outbox.OutboxPayload
import dev.msj.journal.core.outbox.OutboxRecord
import dev.msj.journal.core.outbox.OutboxState
import dev.msj.journal.core.outbox.Reconciler
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.util.Locale
import kotlin.random.Random

/**
 * One upload run over the outbox, for a signed-in [api]. Only one run exists
 * at a time (the unique work chain), so any UPLOADING record found at the
 * start was cut short by an earlier run and is recovered as UNCERTAIN.
 * An instance performs a single run.
 */
internal class OutboxUploader(
    private val store: OutboxStore,
    private val files: OutboxFiles,
    private val api: JournalApi,
    private val clock: () -> Long,
    private val jitter: () -> Double = Random.Default::nextDouble,
) {
    /** What one run did, for logs and tests. */
    data class Summary(
        val recovered: Int,
        val created: Int,
        val reconciled: Int,
        val failed: Int,
        /** The error that ended the run early (the server is unreachable or the token was rejected), if any. */
        val stoppedBy: ApiError?,
    )

    private enum class Next { CONTINUE, STOP }

    private var started = false
    private var created = 0
    private var reconciled = 0
    private var failed = 0
    private var stoppedBy: ApiError? = null

    suspend fun run(): Summary {
        check(!started) { "an OutboxUploader performs a single run" }
        started = true
        val recovered = store.recoverInterrupted(clock())
        val claimedEntryIds = store.sentServerEntryIds().toMutableSet()
        for (snapshot in store.waiting()) {
            // Re-read: the user may have discarded or retried it since the snapshot.
            val record = store.get(snapshot.id) ?: continue
            if (!OutboxMachine.isDue(record, clock())) continue
            if (process(record, claimedEntryIds) == Next.STOP) break
        }
        purge()
        return Summary(recovered, created, reconciled, failed, stoppedBy)
    }

    private suspend fun process(
        record: OutboxRecord,
        claimedEntryIds: MutableSet<String>,
    ): Next {
        if (record.state == OutboxState.UNCERTAIN) {
            val candidates =
                try {
                    candidatesFor(record.entryDate)
                } catch (error: ApiException) {
                    transition(record.id, OutboxEvent.ReconcileFailed(error.error), OutboxState.UNCERTAIN)
                    return stopIfEveryRecordWouldFail(error.error)
                }
            val match = Reconciler.findMatch(record, candidates, claimedEntryIds)
            if (match != null) {
                val sent = transition(record.id, OutboxEvent.Reconciled(match), OutboxState.UNCERTAIN)
                if (sent != null) {
                    reconciled++
                    claimedEntryIds += match
                    deleteRecording(sent)
                }
                return Next.CONTINUE
            }
            if (OutboxMachine.keepsStopping(record)) {
                // Not on the server, and every recent attempt was cut short: another try would most likely be too.
                if (transition(record.id, keepsStoppingEvent(record), OutboxState.UNCERTAIN) != null) failed++
                return Next.CONTINUE
            }
        }
        return upload(record, claimedEntryIds)
    }

    private fun keepsStoppingEvent(record: OutboxRecord): OutboxEvent.Unsendable {
        val sizeBytes =
            when (val payload = record.payload) {
                is OutboxPayload.Audio -> {
                    payload.sizeBytes
                }

                is OutboxPayload.Text -> {
                    payload.text
                        .toByteArray(Charsets.UTF_8)
                        .size
                        .toLong()
                }
            }
        val sizeMb = "%.1f".format(Locale.ROOT, sizeBytes / BYTES_PER_MB)
        return OutboxEvent.Unsendable(
            code = OutboxCodes.UPLOAD_KEEPS_STOPPING,
            message =
                "Upload stopped: sending this $sizeMb MB entry was cut short ${record.consecutiveInterruptions} " +
                    "times in a row (${record.attempts} attempts in all), so it is not retried automatically.",
            hint = "Connect to a faster network (for example Wi-Fi with the VPN up), then press Retry.",
            details =
                listOf(
                    DetailLine("Size (bytes)", sizeBytes.toString()),
                    DetailLine("Interrupted attempts in a row", record.consecutiveInterruptions.toString()),
                    DetailLine("Attempts", record.attempts.toString()),
                ),
        )
    }

    private suspend fun upload(
        record: OutboxRecord,
        claimedEntryIds: MutableSet<String>,
    ): Next {
        val missing = missingRecording(record.payload)
        if (missing != null) {
            if (transition(record.id, missing, record.state) != null) failed++
            return Next.CONTINUE
        }
        val claimed = transition(record.id, OutboxEvent.Claim, record.state) ?: return Next.CONTINUE
        val entry: EntryDetail =
            try {
                send(claimed)
            } catch (error: ApiException) {
                val after = transition(claimed.id, OutboxEvent.Failed(error.error), OutboxState.UPLOADING)
                if (after?.state == OutboxState.FAILED) failed++
                return stopIfEveryRecordWouldFail(error.error)
            } catch (error: Throwable) {
                // Cancellation (work stopped) or an unexpected error: the request may have reached the server.
                withContext(NonCancellable) { transition(claimed.id, OutboxEvent.Interrupted, OutboxState.UPLOADING) }
                throw error
            }
        val sent = transition(claimed.id, OutboxEvent.Created(entry.id), OutboxState.UPLOADING)
        if (sent == null) {
            Log.w(TAG, "Outbox record ${claimed.id} was removed while its upload created entry ${entry.id}")
        } else {
            created++
            claimedEntryIds += entry.id
            deleteRecording(sent)
        }
        return Next.CONTINUE
    }

    private suspend fun send(record: OutboxRecord): EntryDetail =
        when (val payload = record.payload) {
            is OutboxPayload.Text -> {
                api.createTextEntry(
                    NewTextEntry(
                        text = payload.text,
                        title = record.title,
                        tags = record.tags,
                        entryDate = record.entryDate,
                    ),
                )
            }

            is OutboxPayload.Audio -> {
                api.createAudioEntry(
                    NewAudioEntry(
                        file = File(payload.path),
                        mimeType = payload.mimeType,
                        fileName = payload.fileName,
                        title = record.title,
                        tags = record.tags,
                        entryDate = record.entryDate,
                    ),
                )
            }
        }

    /** The server's entries for [entryDate], every page. */
    private suspend fun candidatesFor(entryDate: String): List<EntryDetail> {
        val filters = EntryFilters(from = entryDate, to = entryDate)
        val entries = mutableListOf<EntryDetail>()
        while (true) {
            val page = api.listEntriesFull(filters, limit = PAGE_SIZE, offset = entries.size)
            entries += page.items
            if (page.items.isEmpty() || entries.size >= page.total) return entries
        }
    }

    /** An Unsendable event when the recording of [payload] is gone or changed size; null when it can be sent. */
    private fun missingRecording(payload: OutboxPayload): OutboxEvent.Unsendable? {
        if (payload !is OutboxPayload.Audio) return null
        val file = File(payload.path)
        val actualSize = if (file.isFile) file.length() else null
        if (actualSize == payload.sizeBytes) return null
        return OutboxEvent.Unsendable(
            code = OutboxCodes.RECORDING_MISSING,
            message =
                if (actualSize == null) {
                    "Upload recording failed: the saved recording is missing from the phone."
                } else {
                    "Upload recording failed: the saved recording changed size since it was saved."
                },
            hint = "The recording cannot be recovered. Discard this entry and record it again.",
            details =
                listOf(
                    DetailLine("File", payload.path),
                    DetailLine("Expected size (bytes)", payload.sizeBytes.toString()),
                    DetailLine("Actual size (bytes)", actualSize?.toString() ?: "file not found"),
                ),
        )
    }

    /**
     * STOP when [error] means every other record would fail the same way now: no route to the
     * server, a rejected token, or the server's rate limit (429).
     */
    private fun stopIfEveryRecordWouldFail(error: ApiError): Next =
        if (error.delivery == Delivery.NOT_SENT || OutboxMachine.classify(error) == FailureClass.AUTH ||
            error.status == 429
        ) {
            stoppedBy = error
            Next.STOP
        } else {
            Next.CONTINUE
        }

    private suspend fun transition(
        id: String,
        event: OutboxEvent,
        expected: OutboxState,
    ): OutboxRecord? = store.transition(id, event, clock(), jitter()) { it == expected }

    private suspend fun purge() {
        val nowMs = clock()
        store.purgeSentBefore(nowMs - OutboxMachine.SENT_RETENTION_MS).forEach(::deleteRecording)
        val sweep = files.saveLock.withLock { files.deleteOrphans(store.audioPaths(), nowMs) }
        if (sweep.deleted > 0 || sweep.spared > 0) {
            Log.w(
                TAG,
                "Outbox files without a record (a save was cut short): deleted ${sweep.deleted} older than a day, " +
                    "kept ${sweep.spared} younger",
            )
        }
    }

    private fun deleteRecording(record: OutboxRecord) {
        val payload = record.payload as? OutboxPayload.Audio ?: return
        if (!files.delete(payload.path)) {
            Log.w(
                TAG,
                "Could not delete the uploaded recording ${payload.path}; it is retried when the record is purged",
            )
        }
    }

    private companion object {
        const val TAG = "MsjOutbox"
        const val PAGE_SIZE = 500
        const val BYTES_PER_MB = 1024.0 * 1024.0
    }
}
