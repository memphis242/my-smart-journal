package dev.msj.journal

import android.content.Context
import dev.msj.journal.outbox.OutboxRepository
import dev.msj.journal.session.AppPrefs
import dev.msj.journal.session.ConnectionFactory
import dev.msj.journal.session.SessionManager
import java.time.LocalDate

/** The app's long-lived services, created once per process by [JournalApplication]. */
class AppServices(
    val context: Context,
    val prefs: AppPrefs,
    val session: SessionManager,
    val outbox: OutboxRepository,
    val connections: ConnectionFactory,
    /** Wall clock in epoch milliseconds. */
    val clock: () -> Long = System::currentTimeMillis,
    /** The user's local calendar day. */
    val today: () -> LocalDate = LocalDate::now,
)

/** Implemented by the Application; view-model factories and workers reach the services through it. */
interface AppServicesHost {
    val services: AppServices
}

val Context.appServices: AppServices
    get() = (applicationContext as AppServicesHost).services
