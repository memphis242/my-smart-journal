package dev.msj.journal

import android.app.Application
import androidx.work.Configuration
import dev.msj.journal.outbox.OutboxDatabase
import dev.msj.journal.outbox.OutboxDependencies
import dev.msj.journal.outbox.OutboxFiles
import dev.msj.journal.outbox.OutboxHost
import dev.msj.journal.outbox.OutboxWorkerFactory
import dev.msj.journal.outbox.WorkManagerOutboxScheduler
import dev.msj.journal.session.ApiSource
import java.io.File

class JournalApplication :
    Application(),
    OutboxHost,
    Configuration.Provider {
    // --- Outbox wiring -------------------------------------------------------
    // WorkManager is initialized on demand with this configuration (its default
    // initializer is removed in AndroidManifest.xml), so the outbox workers get
    // their dependencies from OutboxWorkerFactory.
    // The ApiSource is a signed-out placeholder until the session layer provides one.
    override val outboxDependencies: OutboxDependencies by lazy {
        val clock: () -> Long = System::currentTimeMillis
        OutboxDependencies(
            apiSource = ApiSource { null },
            database = OutboxDatabase.open(this),
            files = OutboxFiles(File(noBackupFilesDir, "outbox")),
            clock = clock,
            scheduler = WorkManagerOutboxScheduler(this, clock),
        )
    }

    override val workManagerConfiguration: Configuration
        get() =
            Configuration
                .Builder()
                .setWorkerFactory(OutboxWorkerFactory { outboxDependencies })
                .build()
    // --- End of outbox wiring ------------------------------------------------
}
