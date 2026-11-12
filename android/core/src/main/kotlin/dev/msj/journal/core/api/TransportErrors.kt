package dev.msj.journal.core.api

import okhttp3.Call
import okhttp3.EventListener
import java.io.InterruptedIOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.PortUnreachableException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.net.UnknownServiceException
import java.security.cert.CertificateException
import javax.net.ssl.SSLException
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException

/*
 * Classification of transport failures. Delivery is the critical output: the
 * outbox resends NOT_SENT freely and reconciles MAYBE_SENT first, so a failure
 * is NOT_SENT only when OkHttp never began writing the request.
 */

/** Records whether OkHttp began writing the request; attached to the request as a tag. */
internal class CallProbe {
    @Volatile
    var requestStarted: Boolean = false
        private set

    fun markStarted() {
        requestStarted = true
    }
}

internal class ProbeListener(
    private val probe: CallProbe?,
) : EventListener() {
    override fun requestHeadersStart(call: Call) {
        probe?.markStarted()
    }

    companion object {
        val FACTORY: Factory = Factory { call -> ProbeListener(call.request().tag(CallProbe::class.java)) }
    }
}

private const val CHAIN_LIMIT = 6

/** `Class: message` for the throwable and its causes, bounded. */
internal fun causeChain(error: Throwable): String {
    val lines = mutableListOf<String>()
    var current: Throwable? = error
    while (current != null && lines.size < CHAIN_LIMIT) {
        val line = "${current.javaClass.simpleName}: ${current.message ?: "(no message)"}"
        if (line !in lines) lines += line
        current = current.cause?.takeIf { it !== current }
    }
    return bounded(lines.joinToString("\n"), DETAIL_CHARS)
}

private fun chainOf(error: Throwable): List<Throwable> =
    generateSequence(error) {
        it.cause?.takeIf { cause ->
            cause !==
                it
        }
    }.take(CHAIN_LIMIT).toList()

private fun isTimeout(chain: List<Throwable>): Boolean =
    chain.any {
        it is SocketTimeoutException ||
            it is InterruptedIOException
    }

private fun isConnectFailure(chain: List<Throwable>): Boolean =
    chain.any { it is ConnectException || it is NoRouteToHostException || it is PortUnreachableException } ||
        isTimeout(chain)

private fun isTlsFailure(
    chain: List<Throwable>,
    started: Boolean,
): Boolean =
    chain.any { it is SSLHandshakeException || it is SSLPeerUnverifiedException || it is CertificateException } ||
        (!started && chain.any { it is SSLException })

internal fun transportError(
    op: OperationInfo,
    host: String,
    started: Boolean,
    error: Throwable,
): ApiError {
    val chain = chainOf(error)
    val (code, message) =
        when {
            chain.any { it is UnknownHostException } -> {
                ClientCodes.HOST_NOT_FOUND to "the server name '$host' could not be found"
            }

            chain.any {
                it is UnknownServiceException && it.message?.contains(
                    "CLEARTEXT",
                    ignoreCase = true,
                ) == true
            } -> {
                ClientCodes.CLEARTEXT_NOT_PERMITTED to
                    "this app does not allow unencrypted http:// connections to '$host'"
            }

            isTlsFailure(chain, started) -> {
                ClientCodes.TLS_FAILED to "the secure connection to '$host' could not be set up"
            }

            !started && isConnectFailure(chain) -> {
                ClientCodes.HOST_UNREACHABLE to "could not connect to '$host'"
            }

            !started -> {
                ClientCodes.NETWORK_ERROR to "the request could not be sent"
            }

            isTimeout(chain) -> {
                ClientCodes.TIMEOUT to "the server did not answer in time"
            }

            else -> {
                ClientCodes.CONNECTION_LOST to "the connection to the server was lost"
            }
        }
    return ApiError(
        kind = ErrorKind.NETWORK,
        code = code,
        message = "${op.operation} failed: $message.",
        hint = Hints.forCode(code, null),
        details =
            listOf(
                DetailLine("Request", op.requestLine),
                DetailLine("Host", host),
                DetailLine("Cause", causeChain(error)),
            ),
        status = null,
        method = op.method,
        path = op.path,
        retryAfterS = null,
        delivery = if (started) Delivery.MAYBE_SENT else Delivery.NOT_SENT,
    )
}
