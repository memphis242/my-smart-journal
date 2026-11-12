package dev.msj.journal.core.queue

import dev.msj.journal.core.api.ApiError
import dev.msj.journal.core.api.Delivery
import dev.msj.journal.core.api.DetailLine
import dev.msj.journal.core.api.ErrorKind
import dev.msj.journal.core.api.Hints
import dev.msj.journal.core.api.QueueSnapshot
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeParseException

/** Queue interpretation shared with the web client (web/src/state/queue.ts). */
public object QueueLogic {
    public const val FAST_POLL_MS: Long = 2_500
    public const val SLOW_POLL_MS: Long = 20_000

    /** After a batch request, poll quickly until the worker has had time to pick it up. */
    public const val REQUEST_WINDOW_MS: Long = 20_000

    /** Heartbeat age beyond which a server that does not report `worker.stale` is assumed stopped. */
    public const val FALLBACK_STALE_MS: Long = 15_000

    private const val FALLBACK_BATCH_CODE = "BATCH_FAILED"

    /**
     * True when the worker is not running. The server's own judgement wins
     * while the snapshot is fresh; once it is older than the staleness window
     * (polls failing), the heartbeat age decides, measured on the server's
     * clock (phone time plus [skewMs], the server clock minus the phone clock
     * seen when the snapshot arrived) so a wrong phone clock cannot flip it.
     */
    public fun workerStale(
        snapshot: QueueSnapshot,
        nowMs: Long,
        skewMs: Long,
        fetchedAtMs: Long,
    ): Boolean {
        val reported = snapshot.worker.stale
        if (reported != null && nowMs - fetchedAtMs <= FALLBACK_STALE_MS) return reported
        val seen = snapshot.worker.lastSeenAt ?: return true
        val seenMs = parseTimestampMs(seen) ?: return true
        return nowMs + skewMs - seenMs > FALLBACK_STALE_MS
    }

    /** Work is moving or about to: poll quickly. A request waiting on a stopped worker is not about to move. */
    public fun isBusy(
        snapshot: QueueSnapshot,
        stale: Boolean,
    ): Boolean = snapshot.batch.running || snapshot.counts.running > 0 || (snapshot.batch.requestPending && !stale)

    /**
     * Delay before the next poll: fast when work is moving or within the
     * window after a batch request ([fastUntilMs]), else slow.
     */
    public fun pollDelayMs(
        snapshot: QueueSnapshot?,
        nowMs: Long,
        skewMs: Long,
        fetchedAtMs: Long?,
        fastUntilMs: Long,
    ): Long {
        val busy = snapshot != null && isBusy(snapshot, workerStale(snapshot, nowMs, skewMs, fetchedAtMs ?: nowMs))
        return if (busy || nowMs < fastUntilMs) FAST_POLL_MS else SLOW_POLL_MS
    }

    /** The instant until which polling stays fast after a batch request made at [nowMs]. */
    public fun fastUntilAfterRequest(nowMs: Long): Long = nowMs + REQUEST_WINDOW_MS

    /**
     * The last batch's failure as a displayable error, or null. It is a
     * RESPONSE error (the server reported it inside a normal answer, not as an
     * HTTP failure) carrying the server's own code and no status.
     */
    public fun lastBatchError(snapshot: QueueSnapshot): ApiError? {
        val failure = snapshot.batch.lastError ?: return null
        val code = if (ApiError.CODE_PATTERN.matches(failure.code)) failure.code else FALLBACK_BATCH_CODE
        val details = mutableListOf(DetailLine("Code", failure.code))
        snapshot.batch.lastFinishedAt?.let { details += DetailLine("Finished", it) }
        return ApiError(
            kind = ErrorKind.RESPONSE,
            code = code,
            message = "The last batch failed: ${failure.detail}",
            hint = Hints.forCode(code, null),
            details = details,
            status = null,
            method = null,
            path = null,
            retryAfterS = null,
            delivery = Delivery.RECEIVED,
        )
    }

    /** Epoch milliseconds of an ISO 8601 timestamp; a timestamp without an offset is read as UTC. Null when unparseable. */
    internal fun parseTimestampMs(text: String): Long? =
        try {
            OffsetDateTime.parse(text).toInstant().toEpochMilli()
        } catch (_: DateTimeParseException) {
            try {
                LocalDateTime.parse(text).toInstant(ZoneOffset.UTC).toEpochMilli()
            } catch (_: DateTimeParseException) {
                null
            }
        }
}
