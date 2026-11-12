package dev.msj.journal.session

import dev.msj.journal.core.api.JournalApi
import dev.msj.journal.core.api.JournalApiClient
import dev.msj.journal.core.api.JournalHttp
import dev.msj.journal.core.api.ServerUrl
import dev.msj.journal.core.api.ServerUrlResult
import okhttp3.OkHttpClient

/**
 * The real server connection: one shared OkHttp client (one connection pool)
 * for every API client. Plain HTTP is accepted only for [cleartextHosts], which
 * the debug build sets to the emulator's host aliases; the network security
 * config enforces the same rule underneath.
 */
class HttpConnectionFactory(
    private val cleartextHosts: Set<String>,
) : ConnectionFactory {
    private val http: OkHttpClient by lazy { JournalHttp.defaultClient() }

    override fun parse(input: String): ParsedServer =
        when (val result = ServerUrl.parse(input, cleartextHosts)) {
            is ServerUrlResult.Valid -> ParsedServer.Valid(base = result.url.toString(), host = result.url.host)
            is ServerUrlResult.Invalid -> ParsedServer.Invalid(result.error)
        }

    override fun create(
        base: String,
        token: String,
    ): JournalApi {
        val result = ServerUrl.parse(base, cleartextHosts)
        check(result is ServerUrlResult.Valid) { "create() takes a base that parse() accepted" }
        return JournalApiClient(result.url, token, http)
    }

    companion object {
        /** The emulator's alias for the development machine, and loopback. */
        val DEBUG_CLEARTEXT_HOSTS: Set<String> = setOf("10.0.2.2", "127.0.0.1")
    }
}
