package dev.msj.journal.outbox

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.ListenableWorker
import androidx.work.WorkerFactory
import androidx.work.WorkerParameters
import dev.msj.journal.session.ApiSource
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext

/** Everything the outbox's background work needs; built once per process by the [OutboxHost]. */
class OutboxDependencies(
    val apiSource: ApiSource,
    val database: OutboxDatabase,
    val files: OutboxFiles,
    val clock: () -> Long,
    val scheduler: OutboxScheduler,
)

/** Implemented by the Application, so workers and the UI share one set of outbox dependencies. */
interface OutboxHost {
    val outboxDependencies: OutboxDependencies
}

/** Builds the outbox workers with their dependencies; returns null for any other worker (WorkManager's default then applies). */
class OutboxWorkerFactory(
    private val dependencies: () -> OutboxDependencies,
) : WorkerFactory() {
    override fun createWorker(
        appContext: Context,
        workerClassName: String,
        workerParameters: WorkerParameters,
    ): ListenableWorker? =
        when (workerClassName) {
            UploadWorker::class.java.name -> UploadWorker(appContext, workerParameters, dependencies())
            WakeupWorker::class.java.name -> WakeupWorker(appContext, workerParameters, dependencies().scheduler)
            else -> null
        }
}

/**
 * One upload run: recovers interrupted uploads (even signed out, since that
 * touches no network and makes them discardable), then, signed in,
 * reconciles uncertain records, uploads every due record oldest first,
 * purges old sent records and, however the run ends, schedules a wakeup for
 * the next due record. Failed uploads are recorded on their records, so the
 * work itself succeeds; it fails only on an unexpected error, which is logged.
 */
class UploadWorker(
    context: Context,
    params: WorkerParameters,
    private val dependencies: OutboxDependencies,
) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val store = OutboxStore(dependencies.database)
        try {
            store.recoverInterrupted(dependencies.clock())
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            Log.e(TAG, "Outbox upload run failed recovering interrupted uploads; the next kick retries", error)
            return Result.failure()
        }
        val api = dependencies.apiSource.current() ?: return Result.success()
        return try {
            val summary = OutboxUploader(store, dependencies.files, api, dependencies.clock).run()
            val stoppedBy = summary.stoppedBy
            if (stoppedBy != null && Log.isLoggable(TAG, Log.INFO)) {
                Log.i(TAG, "Outbox upload run stopped early: ${stoppedBy.code}: ${stoppedBy.message}")
            }
            Result.success()
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            Log.e(TAG, "Outbox upload run failed unexpectedly; the wakeup or next kick starts a new run", error)
            Result.failure()
        } finally {
            withContext(NonCancellable) { scheduleWakeup(store) }
        }
    }

    private suspend fun scheduleWakeup(store: OutboxStore) {
        try {
            dependencies.scheduler.scheduleWakeup(store.nextWakeupAt(dependencies.clock()))
        } catch (error: Exception) {
            Log.e(TAG, "Scheduling the outbox wakeup failed; uploads resume on the next save or retry", error)
        }
    }

    private companion object {
        const val TAG = "MsjOutbox"
    }
}

/** Fires when the earliest waiting record becomes due, and starts an upload run. */
class WakeupWorker(
    context: Context,
    params: WorkerParameters,
    private val scheduler: OutboxScheduler,
) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        scheduler.enqueueUpload()
        return Result.success()
    }
}
