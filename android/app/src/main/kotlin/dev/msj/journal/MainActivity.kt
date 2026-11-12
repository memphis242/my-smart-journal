package dev.msj.journal

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.DisposableEffect
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

    private fun actionFor(intent: Intent?): LaunchAction? =
        when (intent?.action) {
            ACTION_RECORD -> {
                LaunchAction.Record
            }

            ACTION_WRITE -> {
                LaunchAction.Write(sharedText = null)
            }

            Intent.ACTION_SEND -> {
                if (intent.type?.startsWith("text/") == true) {
                    val subject = intent.getStringExtra(Intent.EXTRA_SUBJECT)?.trim().orEmpty()
                    val text =
                        intent
                            .getCharSequenceExtra(Intent.EXTRA_TEXT)
                            ?.toString()
                            ?.trim()
                            .orEmpty()
                    val combined = listOf(subject, text).filter { it.isNotEmpty() }.distinct().joinToString("\n\n")
                    LaunchAction.Write(sharedText = combined.take(MAX_SHARED_CHARS).ifEmpty { null })
                } else {
                    null
                }
            }

            else -> {
                null
            }
        }

    companion object {
        const val ACTION_RECORD: String = "dev.msj.journal.action.RECORD"
        const val ACTION_WRITE: String = "dev.msj.journal.action.WRITE"

        /** Longer shared text is cut to keep the editor responsive. */
        private const val MAX_SHARED_CHARS = 200_000
    }
}
