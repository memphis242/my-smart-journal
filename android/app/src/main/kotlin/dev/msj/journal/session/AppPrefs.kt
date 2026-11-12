package dev.msj.journal.session

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit

/** The theme the user chose; [SYSTEM] follows the phone until they choose. */
enum class ThemeMode { SYSTEM, LIGHT, DARK }

/**
 * Small, non-secret settings. Read synchronously (the theme must be known
 * before the first frame); the access token is not here (see [TokenVault]).
 */
class AppPrefs(
    context: Context,
) {
    private val prefs: SharedPreferences = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    var themeMode: ThemeMode
        get() = prefs.getString(KEY_THEME, null)?.let { stored -> ThemeMode.entries.firstOrNull { it.name == stored } } ?: ThemeMode.SYSTEM
        set(value) = prefs.edit { putString(KEY_THEME, value.name) }

    /** The normalized server base address, for example `https://journal.example.com`. */
    var serverBase: String?
        get() = prefs.getString(KEY_SERVER, null)
        set(value) = prefs.edit { if (value == null) remove(KEY_SERVER) else putString(KEY_SERVER, value) }

    /** Whether the server resolved to a private-network address at sign-in (Android 17 local network gate). */
    var serverIsPrivate: Boolean
        get() = prefs.getBoolean(KEY_SERVER_PRIVATE, false)
        set(value) = prefs.edit { putBoolean(KEY_SERVER_PRIVATE, value) }

    /** The date range the entry list shows (a RangeKey name). */
    var range: String?
        get() = prefs.getString(KEY_RANGE, null)
        set(value) = prefs.edit { if (value == null) remove(KEY_RANGE) else putString(KEY_RANGE, value) }

    /** Forgets the server; keeps the theme, which is a property of the phone, not of the account. */
    fun clearSession() {
        val committed = prefs.edit().remove(KEY_SERVER).remove(KEY_SERVER_PRIVATE).commit()
        check(committed) { "Clearing the saved server address failed: SharedPreferences.commit() returned false" }
    }

    private companion object {
        const val FILE = "msj_prefs"
        const val KEY_THEME = "theme_mode"
        const val KEY_SERVER = "server_base"
        const val KEY_SERVER_PRIVATE = "server_private"
        const val KEY_RANGE = "range"
    }
}
