package dev.msj.journal.core.queue

import dev.msj.journal.core.api.BatchState
import dev.msj.journal.core.api.CodedDetail
import dev.msj.journal.core.api.Delivery
import dev.msj.journal.core.api.ErrorKind
import dev.msj.journal.core.api.QueueCounts
import dev.msj.journal.core.api.QueueSnapshot
import dev.msj.journal.core.api.WorkerState
import org.junit.Test
import java.time.Instant
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class QueueLogicTest {
    private val heartbeat = Instant.parse("2026-11-08T10:00:00Z").toEpochMilli()

    private fun snapshot(
        stale: Boolean? = null,
        lastSeen: String? = "2026-11-08T10:00:00+00:00",
        running: Boolean = false,
        pending: Boolean = false,
        runningJobs: Int = 0,
        lastError: CodedDetail? = null,
    ) = QueueSnapshot(
        batch =
            BatchState(
                running = running,
                requestPending = pending,
                lastError = lastError,
                lastFinishedAt = "2026-11-08T01:02:03+00:00",
            ),
        worker = WorkerState(lastSeenAt = lastSeen, stale = stale, intervalS = 86400.0),
        counts = QueueCounts(queued = 0, running = runningJobs, failed = 0, blocked = 0),
    )

    @Test
    fun `server judgement wins while the snapshot is fresh`() {
        assertFalse(
            QueueLogic.workerStale(snapshot(stale = false), heartbeat + 600_000, 0, heartbeat + 600_000 - 5_000),
        )
        assertTrue(QueueLogic.workerStale(snapshot(stale = true), heartbeat, 0, heartbeat))
    }

    @Test
    fun `an old snapshot is judged by heartbeat age on the server clock`() {
        val s = snapshot(stale = false)
        assertTrue(QueueLogic.workerStale(s, heartbeat + 60_000, 0, heartbeat + 1_000))
        // A phone clock an hour behind does not flip the verdict once the skew is applied.
        val phoneNow = heartbeat + 20_000 - 3_600_000
        assertTrue(QueueLogic.workerStale(s, phoneNow, 3_600_000, phoneNow - 60_000))
        assertFalse(QueueLogic.workerStale(snapshot(), heartbeat + 5_000, 0, heartbeat))
    }

    @Test
    fun `missing or unparseable heartbeat is stale`() {
        assertTrue(QueueLogic.workerStale(snapshot(lastSeen = null), heartbeat, 0, heartbeat))
        assertTrue(QueueLogic.workerStale(snapshot(lastSeen = "yesterday"), heartbeat, 0, heartbeat))
        assertFalse(QueueLogic.workerStale(snapshot(lastSeen = "2026-11-08T10:00:00"), heartbeat + 1_000, 0, heartbeat))
    }

    @Test
    fun `busy while a batch or job runs, or a request waits on a live worker`() {
        assertTrue(QueueLogic.isBusy(snapshot(running = true), stale = true))
        assertTrue(QueueLogic.isBusy(snapshot(runningJobs = 1), stale = true))
        assertTrue(QueueLogic.isBusy(snapshot(pending = true), stale = false))
        assertFalse(QueueLogic.isBusy(snapshot(pending = true), stale = true))
        assertFalse(QueueLogic.isBusy(snapshot(), stale = false))
    }

    @Test
    fun `poll delay is fast when busy or inside the request window`() {
        val idle = snapshot(stale = false)
        assertEquals(QueueLogic.SLOW_POLL_MS, QueueLogic.pollDelayMs(idle, heartbeat, 0, heartbeat, 0))
        assertEquals(
            QueueLogic.FAST_POLL_MS,
            QueueLogic.pollDelayMs(snapshot(running = true), heartbeat, 0, heartbeat, 0),
        )
        val until = QueueLogic.fastUntilAfterRequest(heartbeat)
        assertEquals(QueueLogic.FAST_POLL_MS, QueueLogic.pollDelayMs(idle, heartbeat + 19_999, 0, heartbeat, until))
        assertEquals(QueueLogic.SLOW_POLL_MS, QueueLogic.pollDelayMs(idle, heartbeat + 20_000, 0, heartbeat, until))
        assertEquals(QueueLogic.SLOW_POLL_MS, QueueLogic.pollDelayMs(null, heartbeat, 0, null, 0))
    }

    @Test
    fun `last batch error carries the server code and hint`() {
        assertNull(QueueLogic.lastBatchError(snapshot()))
        val error =
            QueueLogic.lastBatchError(
                snapshot(lastError = CodedDetail("BATCH_LEASE_LOST", "Another worker took the lease.")),
            )!!
        assertEquals("BATCH_LEASE_LOST", error.code)
        assertEquals(ErrorKind.RESPONSE, error.kind)
        assertEquals(Delivery.RECEIVED, error.delivery)
        assertTrue(error.message.contains("Another worker took the lease."))
        assertTrue(error.hint.orEmpty().contains("lease"))
        assertEquals("2026-11-08T01:02:03+00:00", error.details.first { it.label == "Finished" }.value)
    }

    @Test
    fun `a malformed server code falls back instead of crashing`() {
        val error = QueueLogic.lastBatchError(snapshot(lastError = CodedDetail("weird code!", "boom")))!!
        assertEquals("BATCH_FAILED", error.code)
        assertEquals("weird code!", error.details.first { it.label == "Code" }.value)
    }
}
