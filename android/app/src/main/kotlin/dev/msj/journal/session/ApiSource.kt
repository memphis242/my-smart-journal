package dev.msj.journal.session

import dev.msj.journal.core.api.JournalApi

/** The API for the signed-in server, or null while signed out. Background work reads it per run. */
fun interface ApiSource {
    suspend fun current(): JournalApi?
}
