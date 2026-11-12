package dev.msj.journal.outbox

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

/**
 * Row access for the outbox. A record's identity and payload never change
 * after insert; only its progress columns do, and only through
 * [updateProgress], which is conditional on the state the caller read, so two
 * writers can never both claim a record.
 */
@Dao
internal interface OutboxDao {
    /** Returns the new row id, or -1 when a record with this id already exists. */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertOrIgnore(entity: OutboxEntity): Long

    @Query("SELECT * FROM outbox WHERE state != 'SENT' ORDER BY createdAtMs, id")
    fun observeUnsent(): Flow<List<OutboxEntity>>

    @Query("SELECT * FROM outbox WHERE id = :id")
    suspend fun get(id: String): OutboxEntity?

    /** Records the uploader may act on now or later, oldest first; the caller filters by due-ness. */
    @Query("SELECT * FROM outbox WHERE state IN ('PENDING', 'RETRY_WAIT', 'UNCERTAIN') ORDER BY createdAtMs, id")
    suspend fun dueCandidates(): List<OutboxEntity>

    @Query("SELECT * FROM outbox WHERE state = :state ORDER BY createdAtMs, id")
    suspend fun inState(state: String): List<OutboxEntity>

    /** Updates the progress columns of [id] only while it is still in [expectedState]; returns the rows changed (0 or 1). */
    @Query(
        """
        UPDATE outbox SET
            state = :state, attempts = :attempts, nextAttemptAtMs = :nextAttemptAtMs, firstSentAtMs = :firstSentAtMs,
            errorCode = :errorCode, errorMessage = :errorMessage, errorHint = :errorHint,
            errorDetailsJson = :errorDetailsJson, errorStatus = :errorStatus, errorDelivery = :errorDelivery,
            errorAtMs = :errorAtMs, serverEntryId = :serverEntryId, updatedAtMs = :updatedAtMs
        WHERE id = :id AND state = :expectedState
        """,
    )
    suspend fun updateProgress(
        id: String,
        expectedState: String,
        state: String,
        attempts: Int,
        nextAttemptAtMs: Long,
        firstSentAtMs: Long?,
        errorCode: String?,
        errorMessage: String?,
        errorHint: String?,
        errorDetailsJson: String?,
        errorStatus: Int?,
        errorDelivery: String?,
        errorAtMs: Long?,
        serverEntryId: String?,
        updatedAtMs: Long,
    ): Int

    @Query("SELECT serverEntryId FROM outbox WHERE state = 'SENT' AND serverEntryId IS NOT NULL")
    suspend fun sentServerEntryIds(): List<String>

    /** The earliest time after [nowMs] at which a waiting record becomes due, or null when none waits. */
    @Query(
        "SELECT MIN(nextAttemptAtMs) FROM outbox " +
            "WHERE state IN ('RETRY_WAIT', 'UNCERTAIN') AND nextAttemptAtMs > :nowMs",
    )
    suspend fun earliestFutureAttemptAt(nowMs: Long): Long?

    @Query("SELECT * FROM outbox WHERE state = 'SENT' AND updatedAtMs < :cutoffMs")
    suspend fun sentBefore(cutoffMs: Long): List<OutboxEntity>

    @Query("DELETE FROM outbox WHERE state = 'SENT' AND updatedAtMs < :cutoffMs")
    suspend fun deleteSentBefore(cutoffMs: Long): Int

    /** Deletes [id] unless an upload of it is in flight; returns the rows deleted (0 or 1). */
    @Query("DELETE FROM outbox WHERE id = :id AND state != 'UPLOADING'")
    suspend fun deleteUnlessUploading(id: String): Int

    @Query("DELETE FROM outbox")
    suspend fun deleteAll(): Int

    @Query("SELECT audioPath FROM outbox WHERE audioPath IS NOT NULL")
    suspend fun audioPaths(): List<String>
}
