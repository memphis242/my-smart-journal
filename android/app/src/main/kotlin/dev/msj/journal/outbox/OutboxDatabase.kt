package dev.msj.journal.outbox

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.sqlite.db.SupportSQLiteDatabase

/** The outbox's own database, separate from any cache, so clearing a cache can never lose an unsent entry. */
@Database(entities = [OutboxEntity::class], version = 1, exportSchema = true)
abstract class OutboxDatabase : RoomDatabase() {
    internal abstract fun outboxDao(): OutboxDao

    companion object {
        const val FILE_NAME: String = "outbox.db"

        fun open(context: Context): OutboxDatabase =
            Room
                .databaseBuilder(context.applicationContext, OutboxDatabase::class.java, FILE_NAME)
                .addCallback(SecureDeleteCallback)
                .build()
    }
}

/**
 * Turns on `secure_delete`, so deleted entry text is overwritten instead of
 * lingering in free pages. It is set on the connection Room opens first,
 * which is the one writes (and therefore deletes) go through.
 */
internal object SecureDeleteCallback : RoomDatabase.Callback() {
    override fun onOpen(db: SupportSQLiteDatabase) {
        db.query("PRAGMA secure_delete = ON").use { cursor ->
            check(
                cursor.moveToFirst() && cursor.getInt(0) == 1,
            ) { "SQLite refused PRAGMA secure_delete = ON for the outbox" }
        }
    }
}
