package dev.msj.journal.core.api

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** The outcome of [ServerUrl.parse]. */
public sealed interface ServerUrlResult {
    public data class Valid(
        val url: ServerUrl,
    ) : ServerUrlResult

    public data class Invalid(
        val error: ApiError,
    ) : ServerUrlResult
}

/**
 * The journal server's base address, validated and normalized: no trailing
 * slash, no pasted `/api/v1` suffix, no credentials, query or fragment. A
 * reverse-proxy mount path (`https://host/journal`) is kept.
 */
public class ServerUrl private constructor(
    private val base: HttpUrl,
) {
    init {
        assert(base.encodedPath.endsWith("/")) { "base path ends with a slash: $base" }
        assert(base.query == null && base.fragment == null) { "base has no query or fragment: $base" }
    }

    public val host: String get() = base.host

    /**
     * The URL of an API path such as `/entries/abc`: the base, then `/api/v1`,
     * then [path]. [path] is already percent-encoded and starts with `/`.
     */
    public fun api(path: String): HttpUrl {
        assert(path.startsWith("/")) { "API path starts with a slash: $path" }
        return base
            .newBuilder()
            .addEncodedPathSegments("api/v1" + path)
            .build()
    }

    /** The normalized base address, without a trailing slash. */
    override fun toString(): String = base.toString().removeSuffix("/")

    override fun equals(other: Any?): Boolean = other is ServerUrl && other.base == base

    override fun hashCode(): Int = base.hashCode()

    public companion object {
        private const val EXAMPLE = "https://journal.example.com"
        private val API_SUFFIX = listOf("api", "v1")

        /**
         * Parses what the user typed. Only `https` is accepted, except `http`
         * for hosts in [cleartextHosts] (the debug build's emulator and
         * loopback addresses).
         */
        public fun parse(
            input: String,
            cleartextHosts: Set<String>,
        ): ServerUrlResult {
            val text = input.trim()
            if (text.isEmpty()) {
                return invalid("The server address is empty.", "Type the journal's address, for example $EXAMPLE.")
            }
            val withScheme = if (text.contains("://")) text else "https://$text"
            val scheme = withScheme.substringBefore("://").lowercase()
            if (scheme != "https" && scheme != "http") {
                return invalid(
                    "The address uses '$scheme://', but only https:// is supported.",
                    "Type it as $EXAMPLE.",
                    "Address" to text,
                )
            }
            if (withScheme.contains('?') || withScheme.contains('#')) {
                return invalid(
                    "The address contains a query (?...) or fragment (#...), which a server address cannot have.",
                    "Remove everything from the '?' or '#' onward, for example $EXAMPLE.",
                    "Address" to text,
                )
            }
            val authority = withScheme.substringAfter("://").substringBefore('/')
            if (authority.isEmpty()) {
                return invalid("The address has no host name.", "Type it as $EXAMPLE.", "Address" to text)
            }
            if (authority.contains('@')) {
                return invalid(
                    "The address contains a user name or password before '@'.",
                    "Remove the 'user:password@' part; the access token is entered separately. Example: $EXAMPLE.",
                    "Address" to text,
                )
            }
            val parsed =
                withScheme.toHttpUrlOrNull()
                    ?: return invalid(
                        "The address is not a valid web address.",
                        "Type a host name with an optional port, for example $EXAMPLE or https://journal.example.com:8443.",
                        "Address" to text,
                    )
            if (parsed.host.isEmpty()) {
                return invalid("The address has no host name.", "Type it as $EXAMPLE.", "Address" to text)
            }
            if (parsed.scheme == "http" && parsed.host !in cleartextHosts) {
                return invalid(
                    "The address uses http://, which sends the access token and your journal unencrypted.",
                    "Use https://, for example $EXAMPLE.",
                    "Address" to text,
                )
            }
            val segments = parsed.encodedPathSegments.filter { it.isNotEmpty() }
            val mount =
                if (segments.takeLast(API_SUFFIX.size) ==
                    API_SUFFIX
                ) {
                    segments.dropLast(API_SUFFIX.size)
                } else {
                    segments
                }
            val builder = parsed.newBuilder().encodedPath("/")
            for (segment in mount) builder.addEncodedPathSegment(segment)
            // A trailing empty segment keeps the base path slash-terminated.
            builder.addEncodedPathSegment("")
            return ServerUrlResult.Valid(ServerUrl(builder.build()))
        }

        private fun invalid(
            problem: String,
            next: String,
            vararg details: Pair<String, String>,
        ): ServerUrlResult.Invalid =
            ServerUrlResult.Invalid(
                ApiError(
                    kind = ErrorKind.NETWORK,
                    code = ClientCodes.INVALID_SERVER_URL,
                    message = "Server address rejected: $problem",
                    hint = next,
                    details = details.map { DetailLine(it.first, it.second) },
                    status = null,
                    method = null,
                    path = null,
                    retryAfterS = null,
                    delivery = Delivery.NOT_SENT,
                ),
            )
    }
}
