package dev.msj.journal.core.api

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import okhttp3.Response
import java.io.IOException
import java.time.Instant
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import kotlin.math.ceil

/*
 * Mapping of HTTP error responses to ApiError, mirroring #httpError in
 * web/src/api/client.ts.
 */

/** What a request was, for messages: "Load entries", `GET /api/v1/entries`. */
internal class OperationInfo(
    val operation: String,
    val method: String,
    /** Path below `/api/v1`, percent-encoded, without query. */
    val path: String,
) {
    val requestLine: String get() = "$method /api/v1$path"
}

/** A parsed JSON error body. */
internal class Problem(
    val title: String?,
    val detail: String?,
    val code: String?,
    val type: String?,
    val fieldErrors: List<String>,
    val currentVersion: Int?,
) {
    /** The backend's problem shape carries a `code` or a `title`; other JSON objects do not count. */
    val isBackendProblem: Boolean get() = code != null || title != null
}

internal const val BODY_EXCERPT_CHARS: Int = 1200
internal const val DETAIL_CHARS: Int = 600
private const val PEEK_BYTES: Long = 16_384
private const val MESSAGE_REASON_CHARS: Int = 500

/** Bounds a value shown in the details pane. */
internal fun bounded(
    text: String,
    limit: Int,
): String = if (text.length > limit) "${text.take(limit)}\n... (${text.length - limit} more characters)" else text

/** Codes for responses that carry no `code` of their own. */
internal fun codeForStatus(status: Int): String =
    when (status) {
        400 -> "BAD_REQUEST"
        401 -> "UNAUTHORIZED"
        403 -> "FORBIDDEN"
        404 -> "NOT_FOUND"
        409 -> "CONFLICT"
        413 -> "PAYLOAD_TOO_LARGE"
        415 -> "UNSUPPORTED_MEDIA_TYPE"
        422 -> "VALIDATION_ERROR"
        429 -> "RATE_LIMITED"
        502, 503, 504 -> ClientCodes.SERVER_UNAVAILABLE
        else -> if (status >= 500) ClientCodes.SERVER_ERROR else "HTTP_$status"
    }

private fun JsonObject.text(key: String): String? {
    val value = this[key] as? JsonPrimitive ?: return null
    return if (value.isString && value.content.isNotEmpty()) value.content else null
}

private fun fieldError(item: JsonElement): String? {
    val issue = item as? JsonObject ?: return null
    val field = issue.text("field") ?: "(unnamed field)"
    val type = issue.text("type")
    val message = issue.text("message")
    val reason =
        when {
            type != null && message != null -> "$type ($message)"
            else -> type ?: message ?: "invalid"
        }
    return "$field: $reason"
}

/** The body as a [Problem] when it is a JSON object, else null. */
internal fun readProblem(body: String): Problem? {
    val parsed =
        try {
            Json.parseToJsonElement(body)
        } catch (_: IllegalArgumentException) {
            return null
        }
    val record = parsed as? JsonObject ?: return null
    val errors = (record["errors"] as? JsonArray).orEmpty()
    val version = (record["current_version"] as? JsonPrimitive)?.takeIf { !it.isString }?.intOrNull
    return Problem(
        title = record.text("title"),
        detail = record.text("detail"),
        code = record.text("code"),
        type = record.text("type"),
        fieldErrors = errors.mapNotNull(::fieldError),
        currentVersion = version,
    )
}

private fun Response.statusText(): String = "$code $message".trim()

/** The server's clock for this response, from its `Date` header, else the phone's. */
private fun Response.referenceNow(): Instant = headers.getInstant("Date") ?: Instant.now()

/** `Retry-After` as whole seconds (delta-seconds or an HTTP date), or null. */
internal fun retryAfterSeconds(response: Response): Long? {
    val header = response.header("Retry-After")?.trim() ?: return null
    val seconds = header.toDoubleOrNull()
    if (seconds != null) return if (seconds.isFinite() && seconds >= 0) ceil(seconds).toLong() else null
    return try {
        val at = ZonedDateTime.parse(header, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant()
        val millis = at.toEpochMilli() - response.referenceNow().toEpochMilli()
        maxOf(0L, ceil(millis / 1000.0).toLong())
    } catch (_: DateTimeParseException) {
        null
    }
}

/** Up to 16 KiB of the body without consuming it; a failure to read becomes an explanatory text. */
internal fun peekBody(response: Response): String =
    try {
        response.peekBody(PEEK_BYTES).string()
    } catch (e: IOException) {
        "(body unreadable: ${e.javaClass.simpleName}: ${e.message})"
    }

internal fun httpError(
    op: OperationInfo,
    response: Response,
): ApiError {
    val text = peekBody(response)
    val problem = readProblem(text)
    val code = problem?.code ?: codeForStatus(response.code)
    val retryAfterS = retryAfterSeconds(response)
    val reason = problem?.detail ?: problem?.title ?: "HTTP ${response.statusText()}"
    val details =
        mutableListOf(
            DetailLine("Request", op.requestLine),
            DetailLine("Status", response.statusText()),
        )
    if (problem?.title != null && problem.detail != null) details += DetailLine("Title", problem.title)
    if (problem?.type != null && problem.type != "about:blank") details += DetailLine("Type", problem.type)
    if (problem != null &&
        problem.fieldErrors.isNotEmpty()
    ) {
        details += DetailLine("Fields", problem.fieldErrors.joinToString("\n"))
    }
    if (retryAfterS != null) details += DetailLine("Retry after", "$retryAfterS s")
    if (problem == null && text.isNotEmpty()) details += DetailLine("Body", bounded(text, BODY_EXCERPT_CHARS))
    return ApiError(
        kind = ErrorKind.HTTP,
        code = if (ApiError.CODE_PATTERN.matches(code)) code else codeForStatus(response.code),
        message = "${op.operation} failed: ${bounded(reason, MESSAGE_REASON_CHARS)}",
        hint = Hints.forCode(code, retryAfterS),
        details = details,
        status = response.code,
        method = op.method,
        path = op.path,
        retryAfterS = retryAfterS,
        // A body that is not the backend's problem JSON is a proxy or another service: the request may or may not have arrived.
        delivery = if (problem?.isBackendProblem == true) Delivery.RECEIVED else Delivery.MAYBE_SENT,
        currentVersion = if (code == "ENTRY_CHANGED") problem?.currentVersion else null,
    )
}

internal fun responseInvalid(
    op: OperationInfo,
    vararg extra: DetailLine,
): ApiError =
    ApiError(
        kind = ErrorKind.RESPONSE,
        code = ClientCodes.RESPONSE_INVALID,
        message = "${op.operation} failed: the server's answer did not match the expected shape.",
        hint = Hints.forCode(ClientCodes.RESPONSE_INVALID, null),
        details = listOf(DetailLine("Request", op.requestLine)) + extra,
        status = null,
        method = op.method,
        path = op.path,
        retryAfterS = null,
        delivery = Delivery.RECEIVED,
    )
