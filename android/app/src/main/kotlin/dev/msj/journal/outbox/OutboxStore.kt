package dev.msj.journal.outbox

import androidx.room.withTransaction
import dev.msj.journal.core.outbox.OutboxEvent
import dev.msj.journal.core.outbox.OutboxMachine
import dev.msj.journal.core.outbox.OutboxRecord
import dev.msj.journal.core.outbox.OutboxState
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map

/**
 * Record-level, transactional access to the outbox table. Every change of a
 * record goes through [transition] (or [insert]/[delete]), which reads the row,
 * applies [OutboxMachine.transition] and writes back only if the row is still
 * in the state that was read, inside one transaction.
 */
internal class OutboxStore(
    private val database: OutboxDatabase,
) {
    private val dao: OutboxDao = database.outboxDao()

    val unsent: Flow<List<OutboxRecord>> = dao.observeUnsent().map { rows -> rows.map(OutboxEntity::toRecord) }

    /** Inserts [record] unless its id exists; returns whether it was inserted. */
    suspend fun insert(record: OutboxRecord): Boolean = dao.insertOrIgnore(record.toEntity()) != -1L

    suspend fun get(id: String): OutboxRecord? = dao.get(id)?.toRecord()

    /** Records in PENDING, RETRY_WAIT or UNCERTAIN, oldest first. */
    suspend fun waiting(): List<OutboxRecord> = dao.dueCandidates().map(OutboxEntity::toRecord)

    suspend fun sentServerEntryIds(): Set<String> = dao.sentServerEntryIds().toSet()

    /** When the uploader should next run: [nowMs] if a record is PENDING, else the earliest future attempt; null when nothing waits. */
    suspend fun nextWakeupAt(nowMs: Long): Long? = dao.nextWakeupAt(nowMs)

    /** The last time a record became SENT; null while no SENT record is kept. */
    val lastSentAtMs: Flow<Long?> = dao.observeLastSentAt().distinctUntilChanged()

    suspend fun audioPaths(): Set<String> = dao.audioPaths().toSet()

    /**
     * Applies [event] to record [id] when [accept] allows its current state.
     * Returns the record as written, or null when the record is gone, its
     * state was not accepted, or it changed concurrently.
     */
    suspend fun transition(
        id: String,
        event: OutboxEvent,
        nowMs: Long,
        jitter: Double,
        accept: (OutboxState) -> Boolean = { true },
    ): OutboxRecord? =
        database.withTransaction {
            val current = dao.get(id)?.toRecord()
            if (current == null || !accept(current.state)) {
                null
            } else {
                val next = OutboxMachine.transition(current, event, nowMs, jitter)
                if (writeProgress(current.state, next)) next else null
            }
        }

    /** Moves every UPLOADING record to UNCERTAIN (Interrupted); returns how many. */
    suspend fun recoverInterrupted(nowMs: Long): Int =
        database.withTransaction {
            dao
                .inState(OutboxState.UPLOADING.name)
                .map(OutboxEntity::toRecord)
                .count {
                    writeProgress(
                        OutboxState.UPLOADING,
                        OutboxMachine.transition(it, OutboxEvent.Interrupted, nowMs, 0.0),
                    )
                }
        }

    /** Applies UserRetry to every RETRY_WAIT and UNCERTAIN record that is not due yet. */
    suspend fun makeWaitingDue(nowMs: Long) {
        database.withTransaction {
            dao
                .dueCandidates()
                .map(OutboxEntity::toRecord)
                .filter { it.state != OutboxState.PENDING && !OutboxMachine.isDue(it, nowMs) }
                .forEach { record ->
                    val next = OutboxMachine.transition(record, OutboxEvent.UserRetry, nowMs, 0.0)
                    // The row was read in this transaction, so it is still in the state read.
                    check(
                        writeProgress(record.state, next),
                    ) { "outbox record ${record.id} changed inside a transaction" }
                }
        }
    }

    /** Deletes SENT records last changed before [cutoffMs]; returns them so their files can be removed. */
    suspend fun purgeSentBefore(cutoffMs: Long): List<OutboxRecord> =
        database.withTransaction {
            val expired = dao.sentBefore(cutoffMs).map(OutboxEntity::toRecord)
            val deleted = dao.deleteSentBefore(cutoffMs)
            check(deleted == expired.size) { "purged $deleted SENT outbox rows but read ${expired.size}" }
            expired
        }

    /** Deletes record [id] unless it is UPLOADING; the result carries the deleted record so its file can be removed. */
    suspend fun deleteUnlessUploading(id: String): DeleteResult =
        database.withTransaction {
            val current = dao.get(id)?.toRecord()
            when {
                current == null -> DeleteResult.Missing
                dao.deleteUnlessUploading(id) == 1 -> DeleteResult.Deleted(current)
                else -> DeleteResult.Refused(current.state)
            }
        }

    suspend fun deleteAll(): Int = dao.deleteAll()

    /**
     * Copies the write-ahead log into the database file and truncates it, so
     * deleted rows do not survive in the log. Returns false when a reader kept
     * the checkpoint from completing. Must not run inside a transaction.
     */
    fun checkpointAndTruncateLog(): Boolean =
        database.openHelper.writableDatabase.query("PRAGMA wal_checkpoint(TRUNCATE)").use { cursor ->
            check(cursor.moveToFirst()) { "PRAGMA wal_checkpoint returned no row" }
            // Columns: busy, log frames, checkpointed frames; busy = 1 means it could not finish.
            cursor.getInt(0) == 0
        }

    sealed interface DeleteResult {
        data object Missing : DeleteResult

        data class Deleted(
            val record: OutboxRecord,
        ) : DeleteResult

        data class Refused(
            val state: OutboxState,
        ) : DeleteResult
    }

    private suspend fun writeProgress(
        expected: OutboxState,
        next: OutboxRecord,
    ): Boolean {
        val row = next.toEntity()
        val changed =
            dao.updateProgress(
                id = row.id,
                expectedState = expected.name,
                state = row.state,
                attempts = row.attempts,
                nextAttemptAtMs = row.nextAttemptAtMs,
                firstSentAtMs = row.firstSentAtMs,
                errorCode = row.errorCode,
                errorMessage = row.errorMessage,
                errorHint = row.errorHint,
                errorDetailsJson = row.errorDetailsJson,
                errorStatus = row.errorStatus,
                errorDelivery = row.errorDelivery,
                errorAtMs = row.errorAtMs,
                serverEntryId = row.serverEntryId,
                updatedAtMs = row.updatedAtMs,
                consecutiveInterruptions = row.consecutiveInterruptions,
            )
        assert(changed in 0..1) { "a primary-key update changes at most one row, changed $changed" }
        return changed == 1
    }
}
