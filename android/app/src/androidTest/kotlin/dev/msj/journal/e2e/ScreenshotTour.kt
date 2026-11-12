package dev.msj.journal.e2e

import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.msj.journal.MainActivity
import dev.msj.journal.core.api.NewTextEntry
import dev.msj.journal.ui.components.TestTags
import kotlinx.coroutines.runBlocking
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.RuleChain
import org.junit.runner.RunWith

/**
 * A visual tour, not a check: every main screen in light and dark, saved as
 * PNGs to /data/local/tmp/msj-shots on the device for review (adb pull). Runs only
 * with the instrumentation argument `msjScreenshots=1` (scripts/e2e.sh --shots).
 */
@RunWith(AndroidJUnit4::class)
class ScreenshotTour {
    private companion object {
        const val SHOTS = "/data/local/tmp/msj-shots"
    }

    private val compose = createEmptyComposeRule()

    @get:Rule
    val rules: RuleChain = RuleChain.outerRule(E2e.permissions()).around(compose)

    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    /** Captures the screen as the shell user into /data/local/tmp/msj-shots (app data is cleared after each test). */
    private fun shot(name: String) {
        compose.waitForIdle()
        Thread.sleep(600)
        shell("mkdir -p $SHOTS")
        shell("screencap -p $SHOTS/$name.png")
    }

    private fun shell(command: String) {
        val output = instrumentation.uiAutomation.executeShellCommand(command)
        // Reading to the end waits for the command to finish.
        android.os.ParcelFileDescriptor
            .AutoCloseInputStream(output)
            .use { it.readBytes() }
    }

    @Test
    fun tour() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("msjScreenshots") == "1")
        runBlocking {
            E2e.api.createTextEntry(
                NewTextEntry(
                    "Paired with the platform team on the upload retry policy. We settled on exponential backoff " +
                        "capped at thirty minutes, and reconciliation before any resend.\n\nNext: measure the cold start.",
                    "Upload retry policy",
                    listOf("work", "android"),
                    E2e.today,
                ),
            )
        }
        for (mode in listOf("light", "dark")) {
            val scenario = ActivityScenario.launch(MainActivity::class.java)
            if (mode == "light") {
                compose.waitForTag(TestTags.SIGN_IN_SCREEN)
                compose.onNodeWithTag(TestTags.SIGN_IN_URL).performTextInput(E2e.serverUrl)
                shot("01-signin-$mode")
                compose.onNodeWithTag(TestTags.SIGN_IN_TOKEN).performTextInput(E2e.token)
                compose.onNodeWithTag(TestTags.SIGN_IN_SUBMIT).performClick()
            }
            compose.waitForTag(TestTags.HOME_SCREEN, 30_000)
            if (mode == "dark") {
                compose.onNodeWithTag(TestTags.THEME_SWITCH).performClick()
            }
            compose.waitForTag(TestTags.QUEUE_COUNT_PREFIX + "queued", 30_000)
            shot("02-home-$mode")
            val firstRow = compose.onAllNodes(hasTagPrefix(TestTags.ENTRY_ROW_PREFIX)).onFirst()
            firstRow.performClick()
            compose.waitForTag(TestTags.DETAIL_SCREEN)
            Thread.sleep(1_500)
            shot("03-detail-$mode")
            compose.onNodeWithTag(TestTags.DETAIL_RAW_TOGGLE).performClick()
            shot("04-detail-raw-$mode")
            instrumentation.uiAutomation.performGlobalAction(
                android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK,
            )
            compose.waitForTag(TestTags.HOME_SCREEN)
            compose.onNodeWithTag(TestTags.CAPTURE_WRITE).performClick()
            compose.waitForTag(TestTags.COMPOSER_SCREEN)
            shot("05-composer-$mode")
            compose.onNodeWithTag(TestTags.COMPOSER_CLOSE).performClick()
            compose.waitForTag(TestTags.HOME_SCREEN)
            compose.onNodeWithTag(TestTags.CAPTURE_RECORD).performClick()
            compose.waitForTag(TestTags.RECORDER_STOP, 20_000)
            Thread.sleep(1_500)
            shot("06-recorder-$mode")
            compose.onNodeWithTag(TestTags.RECORDER_STOP).performClick()
            compose.waitForTag(TestTags.RECORDER_SAVE)
            shot("07-recorder-review-$mode")
            compose.onNodeWithTag(TestTags.RECORDER_DISCARD).performClick()
            compose.onNodeWithTag(TestTags.CONFIRM_ACTION).performClick()
            compose.waitForTag(TestTags.HOME_SCREEN)
            compose.onNodeWithTag(TestTags.SEARCH_OPEN).performClick()
            compose.onNodeWithTag(TestTags.SEARCH_FIELD).performTextInput("retry")
            compose.onNodeWithTag(TestTags.SEARCH_FIELD).performImeAction()
            compose.waitForTag(TestTags.SEARCH_DONE, 60_000)
            shot("08-search-$mode")
            instrumentation.uiAutomation.performGlobalAction(
                android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK,
            )
            compose.waitForTag(TestTags.HOME_SCREEN)
            compose.onNodeWithTag(TestTags.MENU).performClick()
            shot("09-menu-$mode")
            compose.onNodeWithTag(TestTags.MENU_OUTBOX).performClick()
            compose.waitForTag(TestTags.OUTBOX_SCREEN)
            shot("10-outbox-$mode")
            instrumentation.uiAutomation.performGlobalAction(
                android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK,
            )
            compose.waitForTag(TestTags.HOME_SCREEN)
            compose.onNodeWithTag(TestTags.MENU).performClick()
            compose.onNodeWithTag(TestTags.MENU_SETTINGS).performClick()
            compose.waitForTag(TestTags.MENU_SIGN_OUT)
            shot("11-settings-$mode")
            scenario.close()
        }
    }
}
