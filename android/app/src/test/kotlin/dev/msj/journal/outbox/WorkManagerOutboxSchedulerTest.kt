package dev.msj.journal.outbox

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.work.Configuration
import androidx.work.NetworkType
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.testing.SynchronousExecutor
import androidx.work.testing.WorkManagerTestInitHelper
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import kotlin.test.assertEquals
import kotlin.test.assertTrue

@RunWith(RobolectricTestRunner::class)
class WorkManagerOutboxSchedulerTest {
    private val context: Context = ApplicationProvider.getApplicationContext()
    private val now = 1_700_000_000_000L
    private lateinit var workManager: WorkManager
    private lateinit var scheduler: WorkManagerOutboxScheduler

    @Before
    fun setUp() {
        val configuration = Configuration.Builder().setExecutor(SynchronousExecutor()).build()
        WorkManagerTestInitHelper.initializeTestWorkManager(context, configuration)
        workManager = WorkManager.getInstance(context)
        scheduler = WorkManagerOutboxScheduler(context) { now }
    }

    private suspend fun infos(name: String): List<WorkInfo> = workManager.getWorkInfosForUniqueWorkFlow(name).first()

    @Test
    fun uploadIsOneUniqueNetworkBoundWork() =
        runTest {
            scheduler.enqueueUpload()
            scheduler.enqueueUpload()

            val infos = infos(OutboxScheduler.UPLOAD_WORK)
            assertEquals(1, infos.size, "a waiting run is kept, not duplicated")
            assertEquals(WorkInfo.State.ENQUEUED, infos.single().state)
            assertEquals(NetworkType.CONNECTED, infos.single().constraints.requiredNetworkType)
        }

    @Test
    fun wakeupIsDelayedAtLeastTheMinimumAndCanBeCancelled() =
        runTest {
            scheduler.scheduleWakeup(now + 1)
            assertEquals(
                OutboxScheduler.MIN_WAKEUP_DELAY_MS,
                infos(OutboxScheduler.WAKEUP_WORK).single { !it.state.isFinished }.initialDelayMillis,
            )

            scheduler.scheduleWakeup(now + 600_000)
            val pending = infos(OutboxScheduler.WAKEUP_WORK).filter { !it.state.isFinished }
            assertEquals(
                listOf(600_000L),
                pending.map { it.initialDelayMillis },
                "the later wakeup replaced the earlier",
            )

            scheduler.scheduleWakeup(null)
            assertTrue(infos(OutboxScheduler.WAKEUP_WORK).all { it.state == WorkInfo.State.CANCELLED })
        }
}
