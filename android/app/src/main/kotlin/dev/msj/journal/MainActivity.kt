package dev.msj.journal

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.DisposableEffect
import dev.msj.journal.capture.CaptureShortcuts
import dev.msj.journal.ui.JournalApp
import dev.msj.journal.ui.LaunchAction
import dev.msj.journal.ui.ThemeController
import dev.msj.journal.ui.theme.JournalTheme
import kotlinx.coroutines.flow.MutableStateFlow

/** The single activity. Shortcuts (Record, Write) and shared text arrive as intents. */
class MainActivity : ComponentActivity() {
    private val launches = MutableStateFlow<LaunchAction?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        val services = appServices
        val theme = ThemeController(services.prefs)
        theme.applyBeforeFirstFrame(this)
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) launches.value = actionFor(intent)
        // Rate-limited updates keep the shortcuts already published, which are the same ones.
        CaptureShortcuts.publish(this).let { published ->
            if (!published) Log.w(TAG, "launcher shortcuts not updated (rate limited)")
        }
        setContent {
            val dark = theme.isDark()
            DisposableEffect(dark) {
                val bars =
                    if (dark) {
                        SystemBarStyle.dark(Color.TRANSPARENT)
                    } else {
                        SystemBarStyle.light(Color.TRANSPARENT, Color.TRANSPARENT)
                    }
                enableEdgeToEdge(statusBarStyle = bars, navigationBarStyle = bars)
                onDispose {}
            }
            JournalTheme(dark = dark) {
                JournalApp(
                    services = services,
                    theme = theme,
                    launches = launches,
                    onConsumeLaunch = { launches.value = null },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        actionFor(intent)?.let { launches.value = it }
    }

    private fun actionFor(intent: Intent?): LaunchAction? {
        if (intent == null) return null
        val viaShortcut = intent.component?.className == CaptureShortcuts.ALIAS_CLASS
        return when (intent.action) {
            CaptureShortcuts.ACTION_RECORD -> {
                if (viaShortcut) LaunchAction.Record else null
            }

            CaptureShortcuts.ACTION_WRITE -> {
                if (viaShortcut) LaunchAction.Write(sharedText = null) else null
            }

            Intent.ACTION_SEND -> {
                if (intent.type?.startsWith("text/") ==
                    true
                ) {
                    sharedText(intent)?.let { LaunchAction.Write(it) }
                } else {
                    null
                }
            }

            else -> {
                null
            }
        }
    }

    /** The text another app shared, bounded; null when there is none or the extras cannot be read. */
    private fun sharedText(intent: Intent): String? =
        try {
            val subject = intent.getStringExtra(Intent.EXTRA_SUBJECT)?.trim().orEmpty()
            val text =
                intent
                    .getCharSequenceExtra(Intent.EXTRA_TEXT)
                    ?.toString()
                    ?.trim()
                    .orEmpty()
            listOf(subject, text)
                .filter { it.isNotEmpty() }
                .distinct()
                .joinToString("\n\n")
                .take(MAX_SHARED_CHARS)
                .ifEmpty { null }
        } catch (error: RuntimeException) {
            // Extras from another app that do not unparcel (BadParcelableException and similar).
            Log.w(TAG, "shared text could not be read: ${error.javaClass.simpleName}")
            null
        }

    companion object {
        private const val TAG = "MainActivity"

        /** Longer shared text is cut: it has to fit an editor and the saved state of one screen. */
        private const val MAX_SHARED_CHARS = 50_000
    }
}
