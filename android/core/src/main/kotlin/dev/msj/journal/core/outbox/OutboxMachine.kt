package dev.msj.journal.core.outbox

import dev.msj.journal.core.api.ApiError
import dev.msj.journal.core.api.Delivery
import dev.msj.journal.core.api.ErrorKind

/**
 * The pure outbox state machine drawn in Outbox.kt: failure classification,
 * transitions, backoff and due-ness. It holds no state and does no I/O, so
 * every caller (database, worker, UI) shares one definition of the rules.
 */
public object OutboxMachine {
    /** How long an UNCERTAIN record waits before reconciliation, so the server finishes a request it may still be processing. */
    public const val RECONCILE_DELAY_MS: Long = 20_000L

    /** How long SENT records are kept so reconciliation can exclude the entries they created. */
    public const val SENT_RETENTION_MS: Long = 7L * 24 * 60 * 60 * 1000

    /** Backoff after the first failed attempt. */
    public const val BASE_BACKOFF_MS: Long = 30_000L

    /** Upper bound of the exponential backoff (before jitter). */
    public const val MAX_BACKOFF_MS: Long = 30L * 60 * 1000

    /** Backoff is spread by this fraction either way so records failing together do not retry together. */
    public const val JITTER_FRACTION: Double = 0.2

    /** A server Retry-After above this is treated as this, so a bogus header cannot park a record for years. */
    public const val MAX_RETRY_AFTER_S: Long = 24L * 60 * 60

    private const val MAX_DOUBLINGS = 20

    private const val HTTP_UNAUTHORIZED = 401
    private const val HTTP_TOO_MANY_REQUESTS = 429
    private val CLIENT_ERRORS = 400..499

    /** How a failed attempt with [error] is handled; see [FailureClass]. */
    public fun classify(error: ApiError): FailureClass {
        val status = error.status
        return when {
            error.delivery == Delivery.NOT_SENT -> FailureClass.RETRY

            error.delivery == Delivery.MAYBE_SENT -> FailureClass.RECONCILE

            // The server answered, but not in the promised shape: a 2xx we could not read may still have created the entry.
            error.kind != ErrorKind.HTTP -> FailureClass.RECONCILE

            status == HTTP_UNAUTHORIZED -> FailureClass.AUTH

            status == HTTP_TOO_MANY_REQUESTS -> FailureClass.RETRY

            status != null && status in CLIENT_ERRORS -> FailureClass.PERMANENT

            // 5xx problem responses (creation is one transaction, so nothing was created) and any other status.
            else -> FailureClass.RETRY
        }
    }

    /**
     * Backoff before attempt [attempts] + 1: 30 s doubling per attempt, capped
     * at 30 min, spread by +-20 % using [jitter] in [0, 1), and never shorter
     * than the server's Retry-After ([retryAfterS], seconds).
     */
    public fun backoffMs(
        attempts: Int,
        retryAfterS: Long?,
        jitter: Double,
    ): Long {
        require(attempts >= 1) { "backoff is computed after at least one attempt, got attempts=$attempts" }
        require(jitter >= 0.0 && jitter < 1.0) { "jitter must be in [0, 1), got $jitter" }
        val doublings = (attempts - 1).coerceAtMost(MAX_DOUBLINGS)
        val base = (BASE_BACKOFF_MS shl doublings).coerceAtMost(MAX_BACKOFF_MS)
        val spread = 1.0 + JITTER_FRACTION * (2.0 * jitter - 1.0)
        val jittered = (base * spread).toLong()
        val floor = (retryAfterS ?: 0L).coerceIn(0L, MAX_RETRY_AFTER_S) * 1000L
        val result = maxOf(jittered, floor)
        assert(result > 0) { "backoff is positive: $result" }
        return result
    }

    /** Whether the uploader should act on [record] at [nowMs]. */
    public fun isDue(
        record: OutboxRecord,
        nowMs: Long,
    ): Boolean =
        when (record.state) {
            OutboxState.PENDING -> true
            OutboxState.RETRY_WAIT, OutboxState.UNCERTAIN -> nowMs >= record.nextAttemptAtMs
            OutboxState.UPLOADING, OutboxState.FAILED, OutboxState.SENT -> false
        }

    /** The display form of [error], stamped [atMs]. */
    public fun toOutboxError(
        error: ApiError,
        atMs: Long,
    ): OutboxError =
        OutboxError(
            code = error.code,
            message = error.message,
            hint = error.hint,
            details = error.details,
            status = error.status,
            delivery = error.delivery,
            atMs = atMs,
        )

    /**
     * The record after [event] at [nowMs]; [jitter] in [0, 1) spreads any
     * backoff. A (state, event) pair the diagram does not allow is a
     * programming error and throws [IllegalStateException].
     */
    public fun transition(
        record: OutboxRecord,
        event: OutboxEvent,
        nowMs: Long,
        jitter: Double,
    ): OutboxRecord {
        val state = record.state
        val next =
            when (event) {
                OutboxEvent.Claim -> {
                    requireState(record, event, OutboxState.PENDING, OutboxState.RETRY_WAIT, OutboxState.UNCERTAIN)
                    record.copy(
                        state = OutboxState.UPLOADING,
                        attempts = record.attempts + 1,
                        firstSentAtMs = record.firstSentAtMs ?: nowMs,
                    )
                }

                is OutboxEvent.Created -> {
                    requireState(record, event, OutboxState.UPLOADING)
                    sent(record, event.entryId)
                }

                is OutboxEvent.Failed -> {
                    requireState(record, event, OutboxState.UPLOADING)
                    failed(record, event.error, nowMs, jitter)
                }

                is OutboxEvent.Reconciled -> {
                    requireState(record, event, OutboxState.UNCERTAIN)
                    sent(record, event.entryId)
                }

                is OutboxEvent.ReconcileFailed -> {
                    requireState(record, event, OutboxState.UNCERTAIN)
                    record.copy(
                        nextAttemptAtMs =
                            nowMs + backoffMs(record.attempts.coerceAtLeast(1), event.error.retryAfterS, jitter),
                        lastError = toOutboxError(event.error, nowMs),
                    )
                }

                OutboxEvent.Interrupted -> {
                    requireState(record, event, OutboxState.UPLOADING)
                    record.copy(
                        state = OutboxState.UNCERTAIN,
                        nextAttemptAtMs = nowMs + RECONCILE_DELAY_MS,
                        lastError = interruptedError(nowMs),
                    )
                }

                OutboxEvent.UserRetry -> {
                    requireState(
                        record,
                        event,
                        OutboxState.FAILED,
                        OutboxState.RETRY_WAIT,
                        OutboxState.UNCERTAIN,
                        OutboxState.PENDING,
                    )
                    // An UNCERTAIN record stays UNCERTAIN: it may already exist on the server and is reconciled first.
                    val retryState = if (state == OutboxState.UNCERTAIN) OutboxState.UNCERTAIN else OutboxState.PENDING
                    record.copy(state = retryState, nextAttemptAtMs = 0L)
                }

                is OutboxEvent.Unsendable -> {
                    requireState(record, event, OutboxState.PENDING, OutboxState.RETRY_WAIT, OutboxState.UNCERTAIN)
                    record.copy(
                        state = OutboxState.FAILED,
                        nextAttemptAtMs = 0L,
                        lastError =
                            OutboxError(
                                code = event.code,
                                message = event.message,
                                hint = event.hint,
                                details = event.details,
                                status = null,
                                delivery = Delivery.NOT_SENT,
                                atMs = nowMs,
                            ),
                    )
                }
            }
        return next.copy(updatedAtMs = nowMs)
    }

    private fun sent(
        record: OutboxRecord,
        entryId: String,
    ): OutboxRecord {
        require(entryId.isNotBlank()) { "a created entry has an id (record ${record.id})" }
        return record.copy(state = OutboxState.SENT, serverEntryId = entryId, lastError = null, nextAttemptAtMs = 0L)
    }

    private fun failed(
        record: OutboxRecord,
        error: ApiError,
        nowMs: Long,
        jitter: Double,
    ): OutboxRecord {
        assert(record.attempts >= 1) { "an UPLOADING record has started an attempt (record ${record.id})" }
        val lastError = toOutboxError(error, nowMs)
        return when (classify(error)) {
            FailureClass.RETRY, FailureClass.AUTH -> {
                record.copy(
                    state = OutboxState.RETRY_WAIT,
                    nextAttemptAtMs = nowMs + backoffMs(record.attempts, error.retryAfterS, jitter),
                    lastError = lastError,
                )
            }

            FailureClass.RECONCILE -> {
                record.copy(
                    state = OutboxState.UNCERTAIN,
                    nextAttemptAtMs =
                        nowMs + maxOf(backoffMs(record.attempts, error.retryAfterS, jitter), RECONCILE_DELAY_MS),
                    lastError = lastError,
                )
            }

            FailureClass.PERMANENT -> {
                record.copy(state = OutboxState.FAILED, nextAttemptAtMs = 0L, lastError = lastError)
            }
        }
    }

    private fun interruptedError(nowMs: Long): OutboxError =
        OutboxError(
            code = OutboxCodes.UPLOAD_INTERRUPTED,
            message =
                "Upload was interrupted before the server answered, so the entry may or may not exist on the server.",
            hint = "Nothing to do: the app checks the server for this entry before sending it again.",
            details = emptyList(),
            status = null,
            delivery = Delivery.MAYBE_SENT,
            atMs = nowMs,
        )

    private fun requireState(
        record: OutboxRecord,
        event: OutboxEvent,
        vararg allowed: OutboxState,
    ) {
        check(record.state in allowed) {
            "Outbox event ${event::class.simpleName} is not allowed in state ${record.state} " +
                "(record ${record.id}; allowed states: ${allowed.joinToString()})"
        }
    }
}
