package dev.msj.journal.outbox

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.work.ListenableWorker
import androidx.work.testing.TestListenableWorkerBuilder
import dev.msj.journal.core.api.ApiException
import dev.msj.journal.core.api.ClientCodes
import dev.msj.journal.core.api.Delivery
import dev.msj.journal.core.api.DetailLine
import dev.msj.journal.core.outbox.OutboxCodes
import dev.msj.journal.core.outbox.OutboxError
import dev.msj.journal.core.outbox.OutboxEvent
import dev.msj.journal.core.outbox.OutboxMachine
import dev.msj.journal.core.outbox.OutboxPayload
import dev.msj.journal.core.outbox.OutboxRecord
import dev.msj.journal.core.outbox.OutboxState
import dev.msj.journal.session.ApiSource
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File
import java.io.IOException
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

@RunWith(RobolectricTestRunner::class)
class OutboxTest {
    @get:Rule
    val temp = TemporaryFolder()

    private val context: Context = ApplicationProvider.getApplicationContext()
    private var now = 1_700_000_000_000L
    private val clock: () -> Long = { now }

    private lateinit var database: OutboxDatabase
    private lateinit var outboxDir: File
    private lateinit var files: OutboxFiles
    private lateinit var store: OutboxStore
    private lateinit var scheduler: FakeScheduler
    private lateinit var repository: RoomOutboxRepository
    private lateinit var api: FakeJournalApi

    @Before
    fun setUp() {
        database = Room.inMemoryDatabaseBuilder(context, OutboxDatabase::class.java).build()
        outboxDir = temp.newFolder("outbox")
        files = OutboxFiles(outboxDir)
        store = OutboxStore(database)
        scheduler = FakeScheduler()
        repository = RoomOutboxRepository(database, files, scheduler, clock)
        api = FakeJournalApi(clock)
    }

    @After
    fun tearDown() {
        database.close()
    }

    private suspend fun runUploader(): OutboxUploader.Summary =
        OutboxUploader(store, files, api, clock, jitter = {
            0.5
        }).run()

    private suspend fun saveText(
        id: String,
        text: String = "Walked by the river.",
    ): Boolean = repository.enqueueText(id, text, title = "Morning", tags = listOf("walk"), entryDate = "2026-11-08")

    private fun recording(
        name: String,
        bytes: ByteArray = ByteArray(2048) { it.toByte() },
    ): File = temp.newFile(name).apply { writeBytes(bytes) }

    private suspend fun record(id: String): OutboxRecord = assertNotNull(store.get(id), "record $id exists")

    @Test
    fun textUploadSendsTheRecordAndRemovesItFromUnsent() =
        runTest {
            assertTrue(saveText("r1"))
            assertEquals(1, scheduler.kicks)
            assertEquals(listOf("r1"), repository.unsent.first().map { it.id })

            val summary = runUploader()

            assertEquals(1, summary.created)
            assertEquals(1, api.createCalls.size)
            assertEquals(OutboxState.SENT, record("r1").state)
            assertEquals("srv-1", record("r1").serverEntryId)
            assertTrue(repository.unsent.first().isEmpty())
        }

    @Test
    fun notSentFailureWaitsAndStopsTheRun() =
        runTest {
            saveText("r1")
            now += 1
            saveText("r2", text = "Second.")
            api.beforeCreate = { throw ApiException(networkError(Delivery.NOT_SENT, ClientCodes.HOST_UNREACHABLE)) }

            val summary = runUploader()

            assertEquals(ClientCodes.HOST_UNREACHABLE, summary.stoppedBy?.code)
            assertEquals(1, api.createCalls.size)
            val first = record("r1")
            assertEquals(OutboxState.RETRY_WAIT, first.state)
            assertEquals(now + OutboxMachine.BASE_BACKOFF_MS, first.nextAttemptAtMs)
            assertEquals(OutboxState.PENDING, record("r2").state)
            assertEquals(first.nextAttemptAtMs, store.earliestFutureAttemptAt(now))
        }

    @Test
    fun authFailureStopsTheRun() =
        runTest {
            saveText("r1")
            now += 1
            saveText("r2", text = "Second.")
            api.beforeCreate = { throw ApiException(httpError(401, "UNAUTHORIZED")) }

            val summary = runUploader()

            assertEquals("UNAUTHORIZED", summary.stoppedBy?.code)
            assertEquals(1, api.createCalls.size)
            assertEquals(OutboxState.RETRY_WAIT, record("r1").state)
            assertEquals(OutboxState.PENDING, record("r2").state)
        }

    @Test
    fun maybeSentIsReconciledLaterWithoutASecondPost() =
        runTest {
            saveText("r1")
            api.createThenFail = networkError(Delivery.MAYBE_SENT, ClientCodes.CONNECTION_LOST)

            runUploader()

            val uncertain = record("r1")
            assertEquals(OutboxState.UNCERTAIN, uncertain.state)
            assertEquals(1, api.entries.size)

            now = uncertain.nextAttemptAtMs
            val summary = runUploader()

            assertEquals(1, summary.reconciled)
            assertEquals(1, api.createCalls.size, "no second POST")
            assertEquals(OutboxState.SENT, record("r1").state)
            assertEquals("srv-1", record("r1").serverEntryId)
        }

    @Test
    fun reconcileMissUploadsExactlyOnce() =
        runTest {
            saveText("r1")
            var failNext = true
            api.beforeCreate = {
                if (failNext) {
                    failNext = false
                    throw ApiException(networkError(Delivery.MAYBE_SENT, ClientCodes.TIMEOUT))
                }
            }
            runUploader()
            assertEquals(OutboxState.UNCERTAIN, record("r1").state)

            now = record("r1").nextAttemptAtMs
            runUploader()

            assertEquals(1, api.listCalls)
            assertEquals(2, api.createCalls.size)
            assertEquals(1, api.entries.size)
            assertEquals(OutboxState.SENT, record("r1").state)
        }

    @Test
    fun reconcileFailureKeepsTheRecordUncertain() =
        runTest {
            saveText("r1")
            api.createThenFail = networkError(Delivery.MAYBE_SENT, ClientCodes.TIMEOUT)
            runUploader()
            now = record("r1").nextAttemptAtMs
            api.listFailure = networkError(Delivery.NOT_SENT, ClientCodes.HOST_UNREACHABLE)

            val summary = runUploader()

            assertEquals(ClientCodes.HOST_UNREACHABLE, summary.stoppedBy?.code)
            assertEquals(OutboxState.UNCERTAIN, record("r1").state)
            assertEquals(ClientCodes.HOST_UNREACHABLE, record("r1").lastError?.code)
            assertEquals(1, api.createCalls.size)
        }

    @Test
    fun interruptedUploadIsRecoveredAsUncertainAndNotResentAtOnce() =
        runTest {
            saveText("r1")
            assertNotNull(store.transition("r1", OutboxEvent.Claim, now, 0.5) { it == OutboxState.PENDING })

            val summary = runUploader()

            assertEquals(1, summary.recovered)
            val recovered = record("r1")
            assertEquals(OutboxState.UNCERTAIN, recovered.state)
            assertEquals(now + OutboxMachine.RECONCILE_DELAY_MS, recovered.nextAttemptAtMs)
            assertEquals(OutboxCodes.UPLOAD_INTERRUPTED, recovered.lastError?.code)
            assertTrue(api.createCalls.isEmpty())
        }

    @Test
    fun cancellationDuringUploadLeavesTheRecordUncertain() =
        runTest {
            saveText("r1")
            val started = CompletableDeferred<Unit>()
            api.beforeCreate = {
                started.complete(Unit)
                awaitCancellation()
            }
            val run = launch { runUploader() }
            started.await()

            run.cancelAndJoin()

            assertEquals(OutboxState.UNCERTAIN, record("r1").state)
        }

    @Test
    fun aClaimNeverSucceedsTwice() =
        runTest {
            saveText("r1")
            val accept: (OutboxState) -> Boolean = { it == OutboxState.PENDING }
            assertNotNull(store.transition("r1", OutboxEvent.Claim, now, 0.5, accept))
            assertNull(store.transition("r1", OutboxEvent.Claim, now, 0.5, accept))
            assertEquals(1, record("r1").attempts)
        }

    @Test
    fun permanentFailureKeepsTheErrorForDisplay() =
        runTest {
            saveText("r1")
            val details = listOf(DetailLine("Field", "text"), DetailLine("Type", "string_too_short"))
            api.beforeCreate = { throw ApiException(httpError(422, "VALIDATION_ERROR", details)) }

            val summary = runUploader()

            assertNull(summary.stoppedBy)
            val failed = record("r1")
            assertEquals(OutboxState.FAILED, failed.state)
            assertEquals("VALIDATION_ERROR", failed.lastError?.code)
            assertEquals(422, failed.lastError?.status)
            assertEquals(details, failed.lastError?.details)
            assertFalse(OutboxMachine.isDue(failed, Long.MAX_VALUE))

            repository.retry("r1")
            assertEquals(OutboxState.PENDING, record("r1").state)
        }

    @Test
    fun savingTheSameIdTwiceInsertsOnceAndKeepsOneFile() =
        runTest {
            assertTrue(saveText("t1"))
            assertFalse(saveText("t1", text = "Different."))
            assertEquals(1, repository.unsent.first().size)

            val first = recording("first.ogg")
            assertTrue(repository.enqueueAudio("a1", first, "audio/ogg", 1_000, null, emptyList(), "2026-11-08"))
            val second = recording("second.ogg", ByteArray(10) { 7 })
            assertFalse(repository.enqueueAudio("a1", second, "audio/ogg", 1_000, null, emptyList(), "2026-11-08"))

            assertEquals(listOf("a1.ogg"), outboxDir.list()!!.toList())
            assertFalse(first.exists(), "the first recording was moved into the outbox")
            assertTrue(second.exists(), "a duplicate save leaves the caller's file alone")
            val audio = assertIs<OutboxPayload.Audio>(record("a1").payload)
            assertEquals(2048L, audio.sizeBytes)
            assertEquals(files.sha256(File(audio.path)), audio.sha256)
            assertEquals(2, scheduler.kicks)
        }

    @Test
    fun aFailedSaveGivesTheRecordingBack() =
        runTest {
            val empty = recording("empty.ogg", ByteArray(0))

            assertFailsWith<IOException> {
                repository.enqueueAudio("a1", empty, "audio/ogg", 0, null, emptyList(), "2026-11-08")
            }

            assertTrue(empty.exists())
            assertTrue(outboxDir.list()!!.isEmpty())
            assertNull(store.get("a1"))
            assertEquals(0, scheduler.kicks)
        }

    @Test
    fun audioUploadMatchesTheServerHashAndDeletesTheRecording() =
        runTest {
            repository.enqueueAudio("a1", recording("rec.ogg"), "audio/ogg", 1_000, "Voice", listOf("x"), "2026-11-08")
            val path = assertIs<OutboxPayload.Audio>(record("a1").payload).path

            runUploader()

            assertEquals(OutboxState.SENT, record("a1").state)
            assertFalse(File(path).exists())
        }

    @Test
    fun missingRecordingFailsWithoutAnUpload() =
        runTest {
            repository.enqueueAudio("a1", recording("rec.ogg"), "audio/ogg", 1_000, null, emptyList(), "2026-11-08")
            val path = assertIs<OutboxPayload.Audio>(record("a1").payload).path
            assertTrue(File(path).delete())

            runUploader()

            val failed = record("a1")
            assertEquals(OutboxState.FAILED, failed.state)
            assertEquals(OutboxCodes.RECORDING_MISSING, failed.lastError?.code)
            assertEquals(0, failed.attempts)
            assertTrue(api.createCalls.isEmpty())
        }

    @Test
    fun discardIsRefusedWhileUploading() =
        runTest {
            repository.enqueueAudio("a1", recording("rec.ogg"), "audio/ogg", 1_000, null, emptyList(), "2026-11-08")
            assertNotNull(store.transition("a1", OutboxEvent.Claim, now, 0.5) { it == OutboxState.PENDING })

            assertFalse(repository.discard("a1"))
            assertNotNull(store.get("a1"))

            assertEquals(1, store.recoverInterrupted(now))
            assertTrue(repository.discard("a1"))
            assertNull(store.get("a1"))
            assertTrue(outboxDir.list()!!.isEmpty())
        }

    @Test
    fun retryAllNowMakesWaitingRecordsDue() =
        runTest {
            saveText("r1")
            api.beforeCreate = { throw ApiException(networkError(Delivery.NOT_SENT, ClientCodes.HOST_NOT_FOUND)) }
            runUploader()
            assertFalse(OutboxMachine.isDue(record("r1"), now))

            repository.retryAllNow()

            assertEquals(OutboxState.PENDING, record("r1").state)
            assertTrue(OutboxMachine.isDue(record("r1"), now))
        }

    @Test
    fun wipeDeletesRecordsAndRecordingsAndCancelsWork() =
        runTest {
            saveText("r1")
            repository.enqueueAudio("a1", recording("rec.ogg"), "audio/ogg", 1_000, null, emptyList(), "2026-11-08")

            repository.wipe()

            assertEquals(1, scheduler.cancelled)
            assertTrue(repository.unsent.first().isEmpty())
            assertTrue(outboxDir.list()!!.isEmpty())
        }

    @Test
    fun sentRecordsArePurgedAfterRetention() =
        runTest {
            saveText("r1")
            runUploader()
            assertEquals(setOf("srv-1"), store.sentServerEntryIds())

            now += OutboxMachine.SENT_RETENTION_MS + 1
            runUploader()

            assertNull(store.get("r1"))
        }

    @Test
    fun entityMappingRoundTrips() {
        val error =
            OutboxError(
                code = "VALIDATION_ERROR",
                message = "Create entry failed.",
                hint = "Shorten the title.",
                details = listOf(DetailLine("Field", "title"), DetailLine("Quote \"", "line\nbreak")),
                status = 422,
                delivery = Delivery.RECEIVED,
                atMs = 42L,
            )
        val text =
            OutboxRecord(
                id = "r1",
                createdAtMs = 1L,
                entryDate = "2026-11-08",
                title = null,
                tags = listOf("a", "b, c", "é"),
                payload = OutboxPayload.Text("Line one\nline two "),
                state = OutboxState.FAILED,
                attempts = 3,
                nextAttemptAtMs = 0L,
                firstSentAtMs = 5L,
                lastError = error,
                serverEntryId = null,
                updatedAtMs = 9L,
            )
        val audio =
            text.copy(
                id = "a1",
                title = "Voice",
                tags = emptyList(),
                payload = OutboxPayload.Audio("/x/a1.ogg", "audio/ogg", "a1.ogg", "f".repeat(64), 10L, 1_500L),
                state = OutboxState.SENT,
                firstSentAtMs = null,
                lastError = null,
                serverEntryId = "srv-9",
            )
        assertEquals(text, text.toEntity().toRecord())
        assertEquals(audio, audio.toEntity().toRecord())
    }

    @Test
    fun workerDoesNothingWhileSignedOut() =
        runTest {
            saveText("r1")
            val result = buildWorker(ApiSource { null }).doWork()

            assertIs<ListenableWorker.Result.Success>(result)
            assertEquals(OutboxState.PENDING, record("r1").state)
        }

    @Test
    fun workerUploadsAndSchedulesTheWakeup() =
        runTest {
            saveText("r1")
            now += 1
            saveText("r2", text = "Second.")
            api.beforeCreate = { entry ->
                if (entry is dev.msj.journal.core.api.NewTextEntry && entry.text == "Second.") {
                    throw ApiException(httpError(503, ClientCodes.SERVER_UNAVAILABLE))
                }
            }

            val result = buildWorker(ApiSource { api }).doWork()

            assertIs<ListenableWorker.Result.Success>(result)
            assertEquals(OutboxState.SENT, record("r1").state)
            assertEquals(OutboxState.RETRY_WAIT, record("r2").state)
            assertEquals(record("r2").nextAttemptAtMs, scheduler.wakeupAtMs)
        }

    private fun buildWorker(apiSource: ApiSource): UploadWorker {
        val dependencies = OutboxDependencies(apiSource, database, files, clock, scheduler)
        return TestListenableWorkerBuilder<UploadWorker>(context)
            .setWorkerFactory(OutboxWorkerFactory { dependencies })
            .build()
    }
}
