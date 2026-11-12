package dev.msj.journal.outbox

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/** The outbox's own database, separate from any cache, so clearing a cache can never lose an unsent entry. */
@Database(entities = [OutboxEntity::class], version = 1, exportSchema = true)
abstract class OutboxDatabase : RoomDatabase() {
    internal abstract fun outboxDao(): OutboxDao

    companion object {
        const val FILE_NAME: String = "outbox.db"

        fun open(context: Context): OutboxDatabase =
            Room
                .databaseBuilder(context.applicationContext, OutboxDatabase::class.java, FILE_NAME)
                .build()
    }
}
