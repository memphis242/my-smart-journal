package dev.msj.journal.capture

import android.content.Context
import android.content.Intent
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import dev.msj.journal.R

/**
 * The launcher's Record and Write shortcuts. Published at run time (the
 * package name differs between the debug and release builds) and aimed at the
 * non-exported `.CaptureShortcut` alias, so only the launcher can use them.
 */
object CaptureShortcuts {
    const val ACTION_RECORD: String = "dev.msj.journal.action.RECORD"
    const val ACTION_WRITE: String = "dev.msj.journal.action.WRITE"

    /** The alias class; MainActivity honors the two actions only when started through it. */
    const val ALIAS_CLASS: String = "dev.msj.journal.CaptureShortcut"

    /** Publishes both shortcuts. Returns false when the system rate-limited the update (it keeps the previous ones). */
    fun publish(context: Context): Boolean {
        fun shortcut(
            id: String,
            action: String,
            short: Int,
            long: Int,
            icon: Int,
        ): ShortcutInfoCompat =
            ShortcutInfoCompat
                .Builder(context, id)
                .setShortLabel(context.getString(short))
                .setLongLabel(context.getString(long))
                .setIcon(IconCompat.createWithResource(context, icon))
                .setIntent(Intent(action).setClassName(context.packageName, ALIAS_CLASS))
                .build()
        return ShortcutManagerCompat.setDynamicShortcuts(
            context,
            listOf(
                shortcut(
                    "record",
                    ACTION_RECORD,
                    R.string.shortcut_record_short,
                    R.string.shortcut_record_long,
                    R.drawable.ic_shortcut_record,
                ),
                shortcut(
                    "write",
                    ACTION_WRITE,
                    R.string.shortcut_write_short,
                    R.string.shortcut_write_long,
                    R.drawable.ic_shortcut_write,
                ),
            ),
        )
    }
}

/** Shared text waiting for the composer, kept in memory only: too large for navigation arguments or saved state. */
class SharedTextStore {
    private val pending = HashMap<String, String>()

    @Synchronized
    fun put(text: String): String {
        val id =
            java.util.UUID
                .randomUUID()
                .toString()
        pending[id] = text
        return id
    }

    @Synchronized
    fun take(id: String): String? = pending.remove(id)
}

/** Where recordings wait between Stop and Save: app-private and not evicted under storage pressure (unlike the cache). */
fun recordingStagingDir(context: Context): java.io.File = java.io.File(context.noBackupFilesDir, "recordings")
