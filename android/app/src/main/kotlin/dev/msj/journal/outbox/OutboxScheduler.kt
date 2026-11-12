package dev.msj.journal.outbox

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.await
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.TimeUnit

/** Starts and times the background uploader. */
interface OutboxScheduler {
    /** Starts the uploader soon without waiting; failures are logged. */
    fun kick()

    /** Makes sure an upload run starts (once the network is up) after this call; returns once WorkManager accepted it. */
    suspend fun enqueueUpload()

    /** Wakes the uploader at [atMs] (at least [MIN_WAKEUP_DELAY_MS] from now), or cancels the wakeup when null. */
    suspend fun scheduleWakeup(atMs: Long?)

    /** Cancels the uploader and the wakeup (sign-out). */
    suspend fun cancelAll()

    companion object {
        const val UPLOAD_WORK: String = "msj-outbox-upload"
        const val WAKEUP_WORK: String = "msj-outbox-wakeup"
        const val MIN_WAKEUP_DELAY_MS: Long = 15_000L
    }
}

/**
 * [OutboxScheduler] on WorkManager. The uploader is the unique one-time work
 * [OutboxScheduler.UPLOAD_WORK], so only one upload run exists at a time:
 * - an unfinished, not yet started run (ENQUEUED or BLOCKED) is kept, since it
 *   reads every due record when it starts;
 * - a RUNNING run is never replaced (that would cancel a request mid-flight);
 *   a new run is appended after it instead;
 * - otherwise a new run replaces the finished chain.
 * The decision reads WorkManager's state first, so it can race with a run
 * that is just starting or finishing. That is safe: a cancelled attempt
 * becomes UNCERTAIN and is reconciled before it is sent again.
 */
class WorkManagerOutboxScheduler(
    context: Context,
    private val clock: () -> Long,
) : OutboxScheduler {
    private val appContext: Context = context.applicationContext
    private val workManager: WorkManager by lazy { WorkManager.getInstance(appContext) }
    private val enqueueLock = Mutex()
    private val scope =
        CoroutineScope(
            SupervisorJob() + Dispatchers.IO +
                CoroutineExceptionHandler { _, error -> Log.e(TAG, "Starting the outbox uploader failed", error) },
        )

    private val networkConstraint: Constraints =
        Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    override fun kick() {
        // Fire and forget: the job reports its own failure through the scope's exception handler.
        scope.launch { enqueueUpload() }
    }

    override suspend fun enqueueUpload() {
        enqueueLock.withLock {
            val states = workManager.getWorkInfosForUniqueWorkFlow(OutboxScheduler.UPLOAD_WORK).first().map { it.state }
            val policy =
                when {
                    states.any { it == WorkInfo.State.ENQUEUED || it == WorkInfo.State.BLOCKED } -> return
                    states.any { it == WorkInfo.State.RUNNING } -> ExistingWorkPolicy.APPEND_OR_REPLACE
                    else -> ExistingWorkPolicy.REPLACE
                }
            val request =
                OneTimeWorkRequestBuilder<UploadWorker>()
                    .setConstraints(networkConstraint)
                    .build()
            workManager.enqueueUniqueWork(OutboxScheduler.UPLOAD_WORK, policy, request).await()
        }
    }

    override suspend fun scheduleWakeup(atMs: Long?) {
        if (atMs == null) {
            workManager.cancelUniqueWork(OutboxScheduler.WAKEUP_WORK).await()
            return
        }
        val delayMs = (atMs - clock()).coerceAtLeast(OutboxScheduler.MIN_WAKEUP_DELAY_MS)
        val request =
            OneTimeWorkRequestBuilder<WakeupWorker>()
                .setConstraints(networkConstraint)
                .setInitialDelay(delayMs, TimeUnit.MILLISECONDS)
                .build()
        workManager.enqueueUniqueWork(OutboxScheduler.WAKEUP_WORK, ExistingWorkPolicy.REPLACE, request).await()
    }

    override suspend fun cancelAll() {
        workManager.cancelUniqueWork(OutboxScheduler.UPLOAD_WORK).await()
        workManager.cancelUniqueWork(OutboxScheduler.WAKEUP_WORK).await()
    }

    private companion object {
        const val TAG = "MsjOutbox"
    }
}
