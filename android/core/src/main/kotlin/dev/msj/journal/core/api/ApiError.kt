package dev.msj.journal.core.api

/**
 * Error model shown to the user: a plain message naming the operation, a
 * stable UPPER_SNAKE code, the next step ([hint]), and labelled [details] for
 * the "Show details" expander. Mirrors web/src/api/errors.ts, extended with
 * the transport failures a phone on a mesh VPN meets.
 */
public data class DetailLine(
    val label: String,
    val value: String,
)

public enum class ErrorKind {
    /** The server answered with an error status. */
    HTTP,

    /** No usable answer arrived: name lookup, connect, TLS, timeout or a dropped connection. */
    NETWORK,

    /** The server answered, but not with the shape the contract promises. */
    RESPONSE,
}

/**
 * Whether the server may have acted on the request. The outbox retries
 * [NOT_SENT] freely, reconciles before retrying [MAYBE_SENT], and never
 * retries a [RECEIVED] rejection without a change.
 */
public enum class Delivery {
    /** Failed before any request byte left the phone (lookup, connect, TLS, cleartext policy). */
    NOT_SENT,

    /** The request may have been processed: timeout or dropped connection after sending, or a proxy error page. */
    MAYBE_SENT,

    /** The journal server itself answered (a problem response or an unexpected body). */
    RECEIVED,
}

/** Codes the client produces itself; server codes come from the problem body. */
public object ClientCodes {
    public const val HOST_NOT_FOUND: String = "HOST_NOT_FOUND"
    public const val HOST_UNREACHABLE: String = "HOST_UNREACHABLE"
    public const val TLS_FAILED: String = "TLS_FAILED"
    public const val CLEARTEXT_NOT_PERMITTED: String = "CLEARTEXT_NOT_PERMITTED"
    public const val TIMEOUT: String = "TIMEOUT"
    public const val CONNECTION_LOST: String = "CONNECTION_LOST"
    public const val NETWORK_ERROR: String = "NETWORK_ERROR"
    public const val NOT_A_JOURNAL_SERVER: String = "NOT_A_JOURNAL_SERVER"
    public const val RESPONSE_INVALID: String = "RESPONSE_INVALID"
    public const val STREAM_INTERRUPTED: String = "STREAM_INTERRUPTED"
    public const val STREAM_INCOMPLETE: String = "STREAM_INCOMPLETE"
    public const val SERVER_UNAVAILABLE: String = "SERVER_UNAVAILABLE"
    public const val SERVER_ERROR: String = "SERVER_ERROR"
    public const val INVALID_SERVER_URL: String = "INVALID_SERVER_URL"
    public const val NOT_SIGNED_IN: String = "NOT_SIGNED_IN"
}

public data class ApiError(
    val kind: ErrorKind,
    val code: String,
    /** One sentence: what failed and why, naming the operation ("Upload recording failed: ..."). */
    val message: String,
    /** What to do next, or null when there is nothing specific to say. */
    val hint: String?,
    val details: List<DetailLine>,
    /** HTTP status; null for network and local failures. */
    val status: Int?,
    val method: String?,
    /** Path below the API prefix, for example `/entries`. */
    val path: String?,
    /** Seconds from a `Retry-After` header, when present. */
    val retryAfterS: Long?,
    val delivery: Delivery,
    /** For 409 ENTRY_CHANGED: the entry's version on the server, when the problem body says. */
    val currentVersion: Int? = null,
) {
    init {
        assert(CODE_PATTERN.matches(code)) { "error code must be UPPER_SNAKE: $code" }
        assert(kind != ErrorKind.HTTP || status != null) { "an HTTP error carries its status" }
    }

    public companion object {
        public val CODE_PATTERN: Regex = Regex("^[A-Z][A-Z0-9_]*$")
    }
}

/** Thrown by [JournalApi]; carries the displayable [error] and keeps the original as [cause]. */
public class ApiException(
    public val error: ApiError,
    cause: Throwable? = null,
) : Exception("${error.code}: ${error.message}", cause)
