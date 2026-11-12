package dev.msj.journal.core.outbox

import dev.msj.journal.core.api.ApiError
import dev.msj.journal.core.api.Delivery
import dev.msj.journal.core.api.ErrorKind

internal fun textRecord(
    state: OutboxState = OutboxState.PENDING,
    attempts: Int = 0,
    text: String = "Walked by the river.",
    title: String? = "Morning",
    tags: List<String> = listOf("walk", "river"),
    entryDate: String = "2026-11-08",
    firstSentAtMs: Long? = null,
    nextAttemptAtMs: Long = 0L,
    serverEntryId: String? = null,
): OutboxRecord =
    OutboxRecord(
        id = "rec-1",
        createdAtMs = 1_000L,
        entryDate = entryDate,
        title = title,
        tags = tags,
        payload = OutboxPayload.Text(text),
        state = state,
        attempts = attempts,
        nextAttemptAtMs = nextAttemptAtMs,
        firstSentAtMs = firstSentAtMs,
        lastError = null,
        serverEntryId = serverEntryId,
        updatedAtMs = 1_000L,
    )

internal fun httpError(
    status: Int,
    code: String = "SOME_ERROR",
    retryAfterS: Long? = null,
    delivery: Delivery = Delivery.RECEIVED,
): ApiError =
    ApiError(
        kind = ErrorKind.HTTP,
        code = code,
        message = "Create entry failed: HTTP $status.",
        hint = null,
        details = emptyList(),
        status = status,
        method = "POST",
        path = "/entries",
        retryAfterS = retryAfterS,
        delivery = delivery,
    )

internal fun networkError(
    delivery: Delivery,
    code: String,
): ApiError =
    ApiError(
        kind = ErrorKind.NETWORK,
        code = code,
        message = "Create entry failed: $code.",
        hint = null,
        details = emptyList(),
        status = null,
        method = "POST",
        path = "/entries",
        retryAfterS = null,
        delivery = delivery,
    )
