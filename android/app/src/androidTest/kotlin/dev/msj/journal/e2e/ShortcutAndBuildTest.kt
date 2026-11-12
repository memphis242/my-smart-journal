package dev.msj.journal.e2e

import android.content.Context
import android.content.Intent
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.msj.journal.MainActivity
import dev.msj.journal.capture.CaptureShortcuts
import dev.msj.journal.ui.UserError
import dev.msj.journal.ui.components.TestTags
import org.junit.Rule
import org.junit.Test
import org.junit.rules.RuleChain
import org.junit.runner.RunWith
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** Launcher shortcuts and build properties that only a device can confirm. */
@RunWith(AndroidJUnit4::class)
class ShortcutAndBuildTest {
    private val compose = createEmptyComposeRule()

    @get:Rule
    val rules: RuleChain = RuleChain.outerRule(E2e.permissions()).around(compose)

    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun appAssertionsRunInTheDebugBuild() {
        // UserError asserts its code is UPPER_SNAKE; the debug build must enforce it.
        assertFailsWith<AssertionError> { UserError("not a code", "message", null, emptyList()) }
    }

    @Test
    fun shortcutsArePublishedForThisPackageThroughTheAlias() {
        ActivityScenario.launch(MainActivity::class.java).close()
        val shortcuts = ShortcutManagerCompat.getDynamicShortcuts(context).associateBy { it.id }
        assertEquals(setOf("record", "write"), shortcuts.keys)
        shortcuts.values.forEach { shortcut ->
            val component = shortcut.intent.component
            assertEquals(context.packageName, component?.packageName)
            assertEquals(CaptureShortcuts.ALIAS_CLASS, component?.className)
        }
    }

    @Test
    fun recordShortcutOpensTheRecorderOnlyThroughTheAlias() {
        E2e.signInDirectly()
        val direct = Intent(CaptureShortcuts.ACTION_RECORD).setClassName(context, MainActivity::class.java.name)
        ActivityScenario.launch<MainActivity>(direct).use {
            compose.waitForTag(TestTags.HOME_SCREEN, 30_000)
            assertTrue(
                compose
                    .onAllNodes(
                        androidx.compose.ui.test
                            .hasTestTag(TestTags.RECORDER_SCREEN),
                    ).fetchSemanticsNodes()
                    .isEmpty(),
            )
        }
        val viaAlias = Intent(CaptureShortcuts.ACTION_RECORD).setClassName(context, CaptureShortcuts.ALIAS_CLASS)
        ActivityScenario.launch<MainActivity>(viaAlias).use {
            compose.waitForTag(TestTags.RECORDER_SCREEN, 30_000)
        }
    }
}
