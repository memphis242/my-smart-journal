package dev.msj.journal.ui

import android.app.Activity
import android.app.UiModeManager
import android.content.res.Configuration
import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.toArgb
import androidx.core.graphics.drawable.toDrawable
import dev.msj.journal.session.AppPrefs
import dev.msj.journal.session.ThemeMode
import dev.msj.journal.ui.theme.DarkColors
import dev.msj.journal.ui.theme.LightColors

/**
 * Light or dark: follows the phone until the user chooses, then remembers the
 * choice. Applied before the first frame (the app's night mode on Android 12+,
 * the window background before that), so the other mode never flashes.
 */
class ThemeController(
    private val prefs: AppPrefs,
) {
    var mode: ThemeMode by mutableStateOf(prefs.themeMode)
        private set

    @Composable
    fun isDark(): Boolean =
        when (mode) {
            ThemeMode.SYSTEM -> isSystemInDarkTheme()
            ThemeMode.LIGHT -> false
            ThemeMode.DARK -> true
        }

    /** Called from onCreate before the first frame. */
    fun applyBeforeFirstFrame(activity: Activity) {
        applyNightMode(activity)
        val systemDark =
            (activity.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
                Configuration.UI_MODE_NIGHT_YES
        val dark = mode == ThemeMode.DARK || (mode == ThemeMode.SYSTEM && systemDark)
        activity.window.setBackgroundDrawable((if (dark) DarkColors else LightColors).bg.toArgb().toDrawable())
    }

    /** Switches to the other mode from the one on screen and remembers it. */
    fun toggle(
        activity: Activity,
        currentlyDark: Boolean,
    ) {
        mode = if (currentlyDark) ThemeMode.LIGHT else ThemeMode.DARK
        prefs.themeMode = mode
        applyNightMode(activity)
    }

    private fun applyNightMode(activity: Activity) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        val manager = activity.getSystemService(UiModeManager::class.java) ?: return
        val wanted =
            when (mode) {
                ThemeMode.SYSTEM -> UiModeManager.MODE_NIGHT_AUTO
                ThemeMode.LIGHT -> UiModeManager.MODE_NIGHT_NO
                ThemeMode.DARK -> UiModeManager.MODE_NIGHT_YES
            }
        manager.setApplicationNightMode(wanted)
    }
}
