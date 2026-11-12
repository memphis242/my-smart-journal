package dev.msj.journal.session

import dev.msj.journal.core.api.ApiError
import dev.msj.journal.core.api.JournalApi

/** The typed server address, checked and normalized. */
sealed interface ParsedServer {
    data class Valid(
        /** Normalized base, for example `https://journal.example.com`. */
        val base: String,
        val host: String,
    ) : ParsedServer

    data class Invalid(
        val error: ApiError,
    ) : ParsedServer
}

/** Builds API clients; the seam that lets tests and previews use a fake server. */
interface ConnectionFactory {
    fun parse(input: String): ParsedServer

    fun create(
        base: String,
        token: String,
    ): JournalApi
}
