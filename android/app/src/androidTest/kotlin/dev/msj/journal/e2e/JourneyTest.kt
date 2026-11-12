package dev.msj.journal.e2e

import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.msj.journal.MainActivity
import dev.msj.journal.core.api.ArtifactKind
import dev.msj.journal.core.api.EntryFilters
import dev.msj.journal.core.api.EntryPatch
import dev.msj.journal.core.api.EntrySource
import dev.msj.journal.core.api.EntryStatus
import dev.msj.journal.core.api.NewTextEntry
import dev.msj.journal.core.dates.RangeKey
import dev.msj.journal.core.dates.isoDate
import dev.msj.journal.core.dates.presetBounds
import dev.msj.journal.session.SessionManager
import dev.msj.journal.ui.components.EntryIdKey
import dev.msj.journal.ui.components.ErrorCodeKey
import dev.msj.journal.ui.components.StateKey
import dev.msj.journal.ui.components.TestTags
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.RuleChain
import org.junit.runner.RunWith
import java.io.File
import java.time.LocalDate
import java.util.UUID
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * The phone journeys end to end against the real backend and worker (fake
 * model routes): capture through the outbox, the list, detail and stepping,
 * Run now with the queue counts, search, the edit conflict, the failed-upload
 * list and sign-out. Server state is read through the API only to learn ids
 * and expected neighbors; the UI is driven and checked through test tags.
 */
@RunWith(AndroidJUnit4::class)
class JourneyTest {
    private val compose = createEmptyComposeRule()

    @get:Rule
    val rules: RuleChain = RuleChain.outerRule(E2e.permissions()).around(compose)

    private var scenario: ActivityScenario<MainActivity>? = null
    private val run = UUID.randomUUID().toString().take(8)

    @Before
    fun signIn() {
        E2e.signInDirectly()
    }

    @After
    fun close() {
        scenario?.close()
    }

    private fun launch() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        compose.waitForTag(TestTags.HOME_SCREEN, 30_000)
    }

    /** Polls [probe] once a second until it returns a value. */
    private fun <T : Any> poll(
        what: String,
        timeoutMs: Long = 60_000,
        probe: () -> T?,
    ): T {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            probe()?.let { return it }
            Thread.sleep(1_000)
        }
        fail("Timed out after $timeoutMs ms waiting for $what")
    }

    private fun seed(
        text: String,
        date: String = E2e.today,
        title: String? = null,
    ): String = runBlocking { E2e.api.createTextEntry(NewTextEntry(text, title, listOf("e2e-android"), date)).id }

    private fun shownEntryIs(id: String): Boolean =
        compose.onAllNodes(SemanticsMatcher.expectValue(EntryIdKey, id)).fetchSemanticsNodes().isNotEmpty()

    @Test
    fun textEntryGoesThroughTheOutboxAppearsAndStepsToItsNeighbors() {
        seed("Older neighbor for $run", date = isoDate(LocalDate.now().minusDays(1)))
        launch()
        val text = "Android e2e text entry $run: reviewed the outbox design."
        compose.onNodeWithTag(TestTags.CAPTURE_WRITE).performClick()
        compose.waitForTag(TestTags.COMPOSER_SCREEN)
        compose.onNodeWithTag(TestTags.COMPOSER_TEXT).performTextInput(text)
        compose.onNodeWithTag(TestTags.COMPOSER_TAGS).performTextInput("e2e-android, outbox")
        compose.onNodeWithTag(TestTags.COMPOSER_SAVE).performClick()
        compose.waitForTag(TestTags.HOME_SCREEN)

        val entry = poll("the entry on the server") { E2e.findEntry { it.rawText() == text } }
        assertEquals(listOf("e2e-android", "outbox"), entry.tags)
        compose.waitForNoTag(TestTags.UNSENT_STRIP, 30_000)
        compose.waitForTag(TestTags.ENTRY_ROW_PREFIX + entry.id, 30_000)
        assertTrue(
            runBlocking {
                E2e.services.outbox.unsent
                    .first()
            }.isEmpty(),
        )

        compose.onNodeWithTag(TestTags.ENTRY_ROW_PREFIX + entry.id).performClick()
        compose.waitForTag(TestTags.DETAIL_SCREEN)
        compose.waitUntil(20_000) { shownEntryIs(entry.id) }

        val week = presetBounds(RangeKey.WEEK, LocalDate.now())
        val expected = runBlocking { E2e.api.neighbors(entry.id, EntryFilters(from = week.fromIso, to = week.toIso)) }
        val older = assertNotNull(expected.older, "the seeded entry gives the new one an older neighbor")
        compose.waitUntil(20_000) { compose.onNodeWithTag(TestTags.DETAIL_OLDER).isEnabled() }
        compose.onNodeWithTag(TestTags.DETAIL_OLDER).performClick()
        compose.waitUntil(20_000) { shownEntryIs(older) && !shownEntryIs(entry.id) }

        // A swipe to the left brings the newer entry back in from the right.
        compose.onNodeWithTag(TestTags.DETAIL_SCREEN).performTouchInput { swipeLeft() }
        compose.waitUntil(20_000) { shownEntryIs(entry.id) && !shownEntryIs(older) }
    }

    @Test
    fun recordedVoiceEntryUploads() {
        launch()
        val title = "Android e2e recording $run"
        compose.onNodeWithTag(TestTags.CAPTURE_RECORD).performClick()
        compose.waitUntil(20_000) {
            compose.onAllNodes(SemanticsMatcher.keyIsDefined(ErrorCodeKey)).fetchSemanticsNodes().isNotEmpty() ||
                compose
                    .onAllNodes(
                        androidx.compose.ui.test
                            .hasTestTag(TestTags.RECORDER_STOP),
                    ).fetchSemanticsNodes()
                    .isNotEmpty()
        }
        val problem =
            compose
                .onAllNodes(
                    SemanticsMatcher.keyIsDefined(ErrorCodeKey),
                ).fetchSemanticsNodes()
                .firstOrNull()
        if (problem != null) fail("The recorder failed with ${problem.config[ErrorCodeKey]}")
        Thread.sleep(2_500)
        compose.onNodeWithTag(TestTags.RECORDER_STOP).performClick()
        compose.waitForTag(TestTags.RECORDER_SAVE)
        compose.onNodeWithTag(TestTags.RECORDER_TITLE).performTextInput(title)
        compose.onNodeWithTag(TestTags.RECORDER_SAVE).performClick()
        compose.waitForTag(TestTags.HOME_SCREEN)

        val entry = poll("the recording on the server") { E2e.findEntry { it.title == title } }
        assertEquals(EntrySource.AUDIO, entry.source)
        val audio = entry.artifacts.single { it.kind == ArtifactKind.RAW_AUDIO }
        assertTrue(
            audio.contentType == "audio/ogg" || audio.contentType == "audio/mp4",
            "stored type ${audio.contentType}",
        )
        compose.waitForTag(TestTags.ENTRY_ROW_PREFIX + entry.id, 30_000)
    }

    @Test
    fun bundledClipUploadsThroughTheOutbox() {
        val title = "Android e2e clip $run"
        val clip = File(E2e.services.context.cacheDir, "e2e-$run.ogg")
        InstrumentationRegistry.getInstrumentation().context.assets.open("e2e-tone.ogg").use { input ->
            clip.outputStream().use { input.copyTo(it) }
        }
        val inserted =
            runBlocking {
                E2e.services.outbox.enqueueAudio(
                    id = UUID.randomUUID().toString(),
                    recording = clip,
                    mimeType = "audio/ogg",
                    durationMs = 1_500,
                    title = title,
                    tags = listOf("e2e-android"),
                    entryDate = E2e.today,
                )
            }
        assertTrue(inserted)
        launch()
        val entry = poll("the clip on the server") { E2e.findEntry { it.title == title } }
        assertEquals(EntrySource.AUDIO, entry.source)
        assertTrue(entry.artifacts.any { it.kind == ArtifactKind.RAW_AUDIO && it.contentType == "audio/ogg" })
        compose.waitForNoTag(TestTags.UNSENT_STRIP, 30_000)
    }

    @Test
    fun runNowProcessesTheQueueAndTheCountsMove() {
        val id = seed("Queue e2e entry $run: something for the worker to process.")
        launch()
        compose.waitForTag(TestTags.QUEUE_COUNT_PREFIX + "queued", 30_000)
        compose.waitUntil(30_000) { compose.countOf(TestTags.QUEUE_COUNT_PREFIX + "queued") > 0 }
        assertTrue(
            compose
                .onAllNodes(
                    androidx.compose.ui.test
                        .hasTestTag(TestTags.QUEUE_STALE),
                ).fetchSemanticsNodes()
                .isEmpty(),
        )

        val deadline = System.currentTimeMillis() + 240_000
        while (true) {
            compose.waitUntil(30_000) { compose.onNodeWithTag(TestTags.QUEUE_RUN_NOW).isEnabled() }
            compose.onNodeWithTag(TestTags.QUEUE_RUN_NOW).performClick()
            val drained =
                runCatching {
                    compose.waitUntil(90_000) {
                        compose.countOf(TestTags.QUEUE_COUNT_PREFIX + "queued") == 0 &&
                            compose.countOf(TestTags.QUEUE_COUNT_PREFIX + "running") == 0
                    }
                }.isSuccess
            if (drained) break
            // A scheduled batch was already running (409 BATCH_ALREADY_RUNNING): request again after it.
            if (System.currentTimeMillis() > deadline) fail("The queue did not drain after Run now")
        }
        val processed = runBlocking { E2e.api.getEntry(id) }
        assertTrue(
            processed.status == EntryStatus.DONE || processed.status == EntryStatus.NEEDS_ATTENTION,
            "status ${processed.status}",
        )
    }

    @Test
    fun searchFindsTheEntryAndOpensIt() {
        val word = "quokkazephyr$run"
        val id = seed("Search e2e entry mentioning $word in passing.")
        launch()
        compose.onNodeWithTag(TestTags.SEARCH_OPEN).performClick()
        compose.waitForTag(TestTags.SEARCH_SCREEN)
        compose.onNodeWithTag(TestTags.SEARCH_FIELD).performTextInput(word)
        compose.onNodeWithTag(TestTags.SEARCH_FIELD).performImeAction()
        compose.waitForTag(TestTags.SEARCH_DONE, 60_000)
        compose.waitForTag(TestTags.SEARCH_RESULT_PREFIX + id)
        compose.onNodeWithTag(TestTags.SEARCH_RESULT_PREFIX + id).performClick()
        compose.waitForTag(TestTags.DETAIL_SCREEN)
        compose.waitUntil(20_000) { shownEntryIs(id) }
    }

    @Test
    fun editConflictReloadsAndReappliesTheChange() {
        val id = seed("Edit e2e entry $run.", title = "Original $run")
        launch()
        compose.waitForTag(TestTags.ENTRY_ROW_PREFIX + id, 30_000)
        compose.onNodeWithTag(TestTags.ENTRY_ROW_PREFIX + id).performClick()
        compose.waitUntil(20_000) { compose.onNodeWithTag(TestTags.DETAIL_EDIT).isEnabled() }
        compose.onNodeWithTag(TestTags.DETAIL_EDIT).performClick()
        compose.waitForTag(TestTags.EDITOR_TITLE)
        compose.onNodeWithTag(TestTags.EDITOR_TITLE).performTextReplacement("Phone title $run")

        // Another device changes the tags meanwhile: the phone's save must not overwrite it blindly.
        runBlocking { E2e.api.patchEntry(id, EntryPatch(tags = EntryPatch.Field.Set(listOf("from-elsewhere")))) }
        compose.onNodeWithTag(TestTags.EDITOR_SAVE).performClick()
        compose.waitForTag(TestTags.EDITOR_CONFLICT)
        compose.onNodeWithTag(TestTags.EDITOR_REAPPLY).performClick()
        compose.onNodeWithTag(TestTags.EDITOR_SAVE).performClick()
        compose.waitForTag(TestTags.DETAIL_SCREEN)

        val saved =
            poll("the reapplied edit") {
                runBlocking { E2e.api.getEntry(id) }.takeIf {
                    it.title ==
                        "Phone title $run"
                }
            }
        assertEquals(listOf("from-elsewhere"), saved.tags)
    }

    @Test
    fun rejectedUploadIsListedWithItsCodeAndCanBeDiscarded() {
        val id = UUID.randomUUID().toString()
        // Year 0 is valid ISO 8601 on the phone but outside Python's date range: the server answers 422,
        // which no retry can fix.
        runBlocking { E2e.services.outbox.enqueueText(id, "Rejected e2e upload $run", null, emptyList(), "0000-01-01") }
        launch()
        compose.waitForTag(TestTags.UNSENT_STRIP, 30_000)
        compose.onNodeWithTag(TestTags.UNSENT_STRIP).performClick()
        compose.waitForTag(TestTags.OUTBOX_ROW_PREFIX + id)
        compose.waitUntil(60_000) {
            compose.onNodeWithTag(TestTags.OUTBOX_ROW_PREFIX + id).fetchSemanticsNode().config[StateKey] == "FAILED"
        }
        compose.waitUntil(10_000) {
            compose
                .onAllNodes(
                    SemanticsMatcher.expectValue(ErrorCodeKey, "VALIDATION_ERROR"),
                ).fetchSemanticsNodes()
                .isNotEmpty()
        }
        compose.onNodeWithTag(TestTags.OUTBOX_DISCARD_PREFIX + id).performClick()
        compose.onNodeWithTag(TestTags.CONFIRM_ACTION).performClick()
        compose.waitForTag(TestTags.OUTBOX_EMPTY)
        assertTrue(
            runBlocking {
                E2e.services.outbox.unsent
                    .first()
            }.isEmpty(),
        )
    }

    @Test
    fun signOutWipesTheSessionAndReturnsToSignIn() {
        launch()
        compose.onNodeWithTag(TestTags.MENU).performClick()
        compose.onNodeWithTag(TestTags.MENU_SETTINGS).performClick()
        compose.waitForTag(TestTags.MENU_SIGN_OUT)
        compose.onNodeWithTag(TestTags.MENU_SIGN_OUT).performClick()
        compose.onNodeWithTag(TestTags.CONFIRM_ACTION).performClick()
        compose.waitForTag(TestTags.SIGN_IN_SCREEN, 30_000)
        assertTrue(E2e.services.session.state.value is SessionManager.State.SignedOut)
        assertEquals(null, E2e.services.prefs.serverBase)
    }
}
