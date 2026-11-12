package dev.msj.journal.e2e

import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextReplacement
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.msj.journal.MainActivity
import dev.msj.journal.session.SessionManager
import dev.msj.journal.ui.components.ErrorCodeKey
import dev.msj.journal.ui.components.StateKey
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.signin.CheckStep
import org.junit.Rule
import org.junit.Test
import org.junit.rules.RuleChain
import org.junit.runner.RunWith
import kotlin.test.assertTrue

/**
 * Sign-in against the real backend, from a fresh install (the orchestrator
 * clears app data before every test): success, and each distinct failure with
 * its own code.
 */
@RunWith(AndroidJUnit4::class)
class SignInTest {
    private val compose = createAndroidComposeRule<MainActivity>()

    @get:Rule
    val rules: RuleChain = RuleChain.outerRule(E2e.permissions()).around(compose)

    private fun fill(
        url: String,
        token: String,
    ) {
        compose.waitForTag(TestTags.SIGN_IN_SCREEN)
        compose.onNodeWithTag(TestTags.SIGN_IN_URL).performTextReplacement(url)
        compose.onNodeWithTag(TestTags.SIGN_IN_TOKEN).performTextReplacement(token)
    }

    private fun waitForError(
        code: String,
        timeoutMs: Long = 30_000,
    ) = compose.waitUntil(timeoutMs) {
        compose.onAllNodes(SemanticsMatcher.expectValue(ErrorCodeKey, code)).fetchSemanticsNodes().isNotEmpty()
    }

    private fun stepState(step: CheckStep): String =
        compose.onNodeWithTag(TestTags.SIGN_IN_STEP_PREFIX + step.name).fetchSemanticsNode().config[StateKey]

    @Test
    fun testConnectionThenSignInOpensTheList() {
        fill(E2e.serverUrl, E2e.token)
        compose.onNodeWithTag(TestTags.SIGN_IN_TEST).performClick()
        compose.waitUntil(30_000) { stepState(CheckStep.TOKEN) == "PASSED" }
        assertTrue(stepState(CheckStep.SERVER) == "PASSED")
        compose.onNodeWithTag(TestTags.SIGN_IN_SUBMIT).performClick()
        compose.waitForTag(TestTags.HOME_SCREEN, 30_000)
        compose.onNodeWithTag(TestTags.QUEUE_STRIP).assertIsDisplayed()
        assertTrue(E2e.services.session.state.value is SessionManager.State.SignedIn)
    }

    @Test
    fun rejectedTokenKeepsTheServerCode() {
        fill(E2e.serverUrl, "not-the-token")
        compose.onNodeWithTag(TestTags.SIGN_IN_SUBMIT).performClick()
        waitForError("UNAUTHORIZED")
        assertTrue(stepState(CheckStep.SERVER) == "PASSED")
        assertTrue(stepState(CheckStep.TOKEN) == "FAILED")
        assertTrue(E2e.services.session.state.value is SessionManager.State.SignedOut)
    }

    @Test
    fun closedPortIsReportedAsUnreachable() {
        fill("http://10.0.2.2:9", E2e.token)
        compose.onNodeWithTag(TestTags.SIGN_IN_TEST).performClick()
        waitForError("HOST_UNREACHABLE", timeoutMs = 45_000)
        assertTrue(stepState(CheckStep.SERVER) == "FAILED")
    }

    @Test
    fun otherServiceIsReportedAsNotAJournalServer() {
        fill("${E2e.serverUrl}/not-the-journal", E2e.token)
        compose.onNodeWithTag(TestTags.SIGN_IN_TEST).performClick()
        waitForError("NOT_A_JOURNAL_SERVER")
    }

    @Test
    fun plainHttpToAnotherHostIsRefusedBeforeConnecting() {
        fill("http://journal.example.com", E2e.token)
        compose.onNodeWithTag(TestTags.SIGN_IN_TEST).performClick()
        waitForError("INVALID_SERVER_URL")
        assertTrue(stepState(CheckStep.ADDRESS) == "FAILED")
    }
}
