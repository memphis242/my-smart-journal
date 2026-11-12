package dev.msj.journal

import android.app.Application
import androidx.work.Configuration
import dev.msj.journal.outbox.OutboxDatabase
import dev.msj.journal.outbox.OutboxDependencies
import dev.msj.journal.outbox.OutboxFiles
import dev.msj.journal.outbox.OutboxHost
import dev.msj.journal.outbox.OutboxWorkerFactory
import dev.msj.journal.outbox.RoomOutboxRepository
import dev.msj.journal.outbox.WorkManagerOutboxScheduler
import dev.msj.journal.session.AppPrefs
import dev.msj.journal.session.HttpConnectionFactory
import dev.msj.journal.session.SessionManager
import dev.msj.journal.session.TokenVault
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import java.io.File

/**
 * Builds the process's services once. WorkManager is initialized on demand
 * with [workManagerConfiguration] (its default initializer is removed in the
 * manifest), so the outbox workers get the same session and database as the UI,
 * also when the process starts only to run an upload.
 */
class JournalApplication :
    Application(),
    AppServicesHost,
    OutboxHost,
    Configuration.Provider {
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val clock: () -> Long = System::currentTimeMillis

    private val prefs: AppPrefs by lazy { AppPrefs(this) }

    private val connections: HttpConnectionFactory by lazy {
        HttpConnectionFactory(if (BuildConfig.DEBUG) HttpConnectionFactory.DEBUG_CLEARTEXT_HOSTS else emptySet())
    }

    private val session: SessionManager by lazy { SessionManager(prefs, TokenVault(this), connections, appScope) }

    override val outboxDependencies: OutboxDependencies by lazy {
        OutboxDependencies(
            apiSource = session,
            database = OutboxDatabase.open(this),
            files = OutboxFiles(File(noBackupFilesDir, "outbox")),
            clock = clock,
            scheduler = WorkManagerOutboxScheduler(this, clock),
        )
    }

    override val services: AppServices by lazy {
        val deps = outboxDependencies
        AppServices(
            context = this,
            prefs = prefs,
            session = session,
            outbox = RoomOutboxRepository(deps.database, deps.files, deps.scheduler, deps.clock),
            connections = connections,
            clock = clock,
        )
    }

    override val workManagerConfiguration: Configuration
        get() =
            Configuration
                .Builder()
                .setWorkerFactory(OutboxWorkerFactory { outboxDependencies })
                .build()
}
