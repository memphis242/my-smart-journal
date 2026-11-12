package dev.msj.journal.ui

import androidx.compose.runtime.Immutable
import dev.msj.journal.core.api.ApiError
import dev.msj.journal.core.api.DetailLine
import dev.msj.journal.core.outbox.OutboxError

/**
 * What an error notice shows: a plain message, a stable code, the next step,
 * and labelled details one step away. Built from API errors, outbox errors and
 * local failures (permissions, recorder, storage) alike.
 */
@Immutable
data class UserError(
    val code: String,
    val message: String,
    val hint: String?,
    val details: List<DetailLine>,
) {
    init {
        assert(ApiError.CODE_PATTERN.matches(code)) { "error code must be UPPER_SNAKE: $code" }
        assert(message.isNotBlank()) { "an error has a message" }
    }

    companion object {
        fun of(error: ApiError): UserError = UserError(error.code, error.message, error.hint, error.details)

        fun of(error: OutboxError): UserError = UserError(error.code, error.message, error.hint, error.details)

        /** For a failure the app did not anticipate; names the operation and keeps the cause in the details. */
        fun unexpected(
            operation: String,
            cause: Throwable,
        ): UserError =
            UserError(
                code = "UNEXPECTED",
                message = "$operation failed unexpectedly.",
                hint = "Try again. If it happens again, the details say where it failed.",
                details =
                    listOf(
                        DetailLine("Cause", "${cause::class.java.name}: ${cause.message ?: "(no message)"}".take(2000)),
                    ),
            )
    }
}
