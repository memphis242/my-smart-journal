package dev.msj.journal.core.api

/**
 * The next step for an error code, mirroring web/src/api/errors.ts and adding
 * the transport codes a phone meets. The server is reachable only over the
 * NetBird mesh VPN, so connectivity hints name it.
 */
public object Hints {
    private val TABLE: Map<String, String> =
        mapOf(
            "BAD_TOKEN" to "Paste the token printed by `msj create-token`. Creating a new token replaces the old one.",
            "UNAUTHORIZED" to "Sign in again with the access token.",
            "TOKEN_REISSUE_REQUIRED" to
                "The access token on the server was replaced. Create a new one on the server with `msj create-token`, " +
                "then sign in with it.",
            "NOT_SIGNED_IN" to "Open the settings and enter the server address and the access token.",
            "INVALID_QUERY" to "Use 1-500 characters of search text, without control characters such as tabs or line breaks.",
            "BATCH_FAILED" to "Check the worker log on the server for the batch error, then run the batch again.",
            "BATCH_LEASE_LOST" to
                "Another worker took over the batch lease; check that only one worker runs, then run the batch again.",
            "WORKER_NOT_RUNNING" to "Start the worker on the server with `msj worker`; it then picks up the request.",
            "CSRF_HEADER_MISSING" to "The server rejected the request as a cross-site request. Update the app and the server to matching versions.",
            "BATCH_ALREADY_RUNNING" to
                "The running batch only processes the work it started with. Run the batch again when the current one finishes.",
            "ENTRY_CHANGED" to "Reload the entry and reapply your changes, then save again.",
            "ENTRY_NOT_FOUND" to "The entry was deleted or never existed. Reload the list.",
            "NOT_FOUND" to "It may have been deleted. Reload the list.",
            "CONFLICT" to "Reload to see the current state, then try again.",
            "PAYLOAD_TOO_LARGE" to "Send a shorter recording, or split it into several entries.",
            "UNSUPPORTED_MEDIA_TYPE" to "Use WAV, MP3, MP4, WebM, Ogg or FLAC audio.",
            "VALIDATION_ERROR" to "Correct the fields listed in the details, then try again.",
            "SERVER_UNAVAILABLE" to "The server or its proxy is down or restarting. Try again in a minute.",
            "SERVER_ERROR" to "Check the service log on the server for this request.",
            "RESPONSE_INVALID" to "The app and the server disagree on the API shape. Update them to matching versions.",
            "EMBEDDINGS_DISABLED" to "Set [embedding] provider to \"sentence-transformers\" in the server config to enable it.",
            "EMBEDDING_DEPENDENCY_MISSING" to
                "Install the semantic extra on the server: pip install my-smart-journal[semantic], then restart.",
            "EMBEDDING_MODEL_LOAD_FAILED" to
                "Check [embedding] model and revision in the server config, the downloaded model cache, and the service log.",
            "EMBEDDING_ENCODING_FAILED" to "The model loaded but could not encode the query; check model compatibility and the service log.",
            "EMBEDDINGS_PENDING" to "Run the batch to embed the remaining entries.",
            ClientCodes.HOST_NOT_FOUND to
                "The server's name could not be resolved. The server is only reachable over the NetBird mesh VPN: " +
                "open the NetBird app and connect, then try again. Also check the address for typos.",
            ClientCodes.HOST_UNREACHABLE to
                "The server did not accept a connection. Check that NetBird is connected and that the journal server is running, " +
                "then try again.",
            ClientCodes.TLS_FAILED to
                "The server needs a publicly trusted certificate for this exact hostname, and the phone's date and time must be correct. " +
                "Check both, and that the address uses the certificate's hostname.",
            ClientCodes.CLEARTEXT_NOT_PERMITTED to "Use an https:// address, for example https://journal.example.com.",
            ClientCodes.TIMEOUT to
                "The server did not answer in time and may have received the request. Check that NetBird is connected, then try again.",
            ClientCodes.CONNECTION_LOST to
                "The connection broke while talking to the server, which may have received the request. " +
                "Check that NetBird is connected, then try again.",
            ClientCodes.NETWORK_ERROR to
                "The request could not be sent. Check the phone's network and that NetBird is connected, then try again.",
            ClientCodes.NOT_A_JOURNAL_SERVER to
                "Enter the journal's base address, for example https://journal.example.com, without a path such as /api or /login.",
            ClientCodes.INVALID_SERVER_URL to "Enter the journal's base address, for example https://journal.example.com.",
            ClientCodes.STREAM_INTERRUPTED to "Search again. If it keeps stopping, check the proxy buffering and the service log.",
            ClientCodes.STREAM_INCOMPLETE to "Search again. If it repeats, check the service log for this request.",
        )

    public fun forCode(
        code: String,
        retryAfterS: Long?,
    ): String? =
        when (code) {
            "RATE_LIMITED" ->
                if (retryAfterS == null) {
                    "Too many attempts. Wait a minute before trying again."
                } else {
                    "Too many attempts. Wait $retryAfterS s before trying again."
                }
            else -> TABLE[code]
        }
}
