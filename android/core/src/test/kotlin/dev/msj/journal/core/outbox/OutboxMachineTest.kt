package dev.msj.journal.core.outbox

import dev.msj.journal.core.api.ApiError
import dev.msj.journal.core.api.ClientCodes
import dev.msj.journal.core.api.Delivery
import dev.msj.journal.core.api.ErrorKind
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class OutboxMachineTest {
    private val now = 1_000_000L
    private val midJitter = 0.5

    @Test
    fun classifiesEveryDeliveryKindAndStatus() {
        val table: List<Pair<ApiError, FailureClass>> =
            listOf(
                httpError(400) to FailureClass.PERMANENT,
                httpError(401, code = "UNAUTHORIZED") to FailureClass.AUTH,
                httpError(404) to FailureClass.PERMANENT,
                httpError(409) to FailureClass.PERMANENT,
                httpError(413, code = "PAYLOAD_TOO_LARGE") to FailureClass.PERMANENT,
                httpError(415, code = "UNSUPPORTED_MEDIA_TYPE") to FailureClass.PERMANENT,
                httpError(422, code = "VALIDATION_ERROR") to FailureClass.PERMANENT,
                httpError(429, code = "RATE_LIMITED", retryAfterS = 60) to FailureClass.RETRY,
                httpError(500, code = "INTERNAL_ERROR") to FailureClass.RETRY,
                httpError(503, code = ClientCodes.SERVER_UNAVAILABLE) to FailureClass.RETRY,
                httpError(502, code = ClientCodes.SERVER_UNAVAILABLE, delivery = Delivery.MAYBE_SENT) to
                    FailureClass.RECONCILE,
                ApiError(
                    kind = ErrorKind.RESPONSE,
                    code = ClientCodes.RESPONSE_INVALID,
                    message = "Create entry failed: the response is missing id.",
                    hint = null,
                    details = emptyList(),
                    status = 201,
                    method = "POST",
                    path = "/entries",
                    retryAfterS = null,
                    delivery = Delivery.RECEIVED,
                ) to FailureClass.RECONCILE,
                networkError(Delivery.NOT_SENT, ClientCodes.HOST_NOT_FOUND) to FailureClass.RETRY,
                networkError(Delivery.NOT_SENT, ClientCodes.HOST_UNREACHABLE) to FailureClass.RETRY,
                networkError(Delivery.NOT_SENT, ClientCodes.TLS_FAILED) to FailureClass.RETRY,
                networkError(Delivery.NOT_SENT, ClientCodes.CLEARTEXT_NOT_PERMITTED) to FailureClass.RETRY,
                networkError(Delivery.MAYBE_SENT, ClientCodes.TIMEOUT) to FailureClass.RECONCILE,
                networkError(Delivery.MAYBE_SENT, ClientCodes.CONNECTION_LOST) to FailureClass.RECONCILE,
            )
        for ((error, expected) in table) {
            assertEquals(expected, OutboxMachine.classify(error), "${error.status} ${error.code} ${error.delivery}")
        }
    }

    @Test
    fun backoffDoublesCapsAndSpreads() {
        assertEquals(30_000L, OutboxMachine.backoffMs(1, null, midJitter))
        assertEquals(60_000L, OutboxMachine.backoffMs(2, null, midJitter))
        assertEquals(OutboxMachine.MAX_BACKOFF_MS, OutboxMachine.backoffMs(7, null, midJitter))
        assertEquals(OutboxMachine.MAX_BACKOFF_MS, OutboxMachine.backoffMs(Int.MAX_VALUE, null, midJitter))
        assertEquals(24_000L, OutboxMachine.backoffMs(1, null, 0.0))
        val high = OutboxMachine.backoffMs(1, null, 0.999_999)
        assertTrue(high in 35_999L..36_000L, "high jitter: $high")
        for (attempts in 1..40) {
            val ms = OutboxMachine.backoffMs(attempts, null, 0.999_999)
            assertTrue(ms <= (OutboxMachine.MAX_BACKOFF_MS * 1.2).toLong(), "bounded at attempts=$attempts: $ms")
        }
    }

    @Test
    fun backoffNeverShorterThanRetryAfter() {
        assertEquals(600_000L, OutboxMachine.backoffMs(1, 600, 0.0))
        assertEquals(30_000L, OutboxMachine.backoffMs(1, 1, midJitter))
        assertEquals(OutboxMachine.MAX_RETRY_AFTER_S * 1000, OutboxMachine.backoffMs(1, Long.MAX_VALUE, midJitter))
        assertFailsWith<IllegalArgumentException> { OutboxMachine.backoffMs(0, null, midJitter) }
        assertFailsWith<IllegalArgumentException> { OutboxMachine.backoffMs(1, null, 1.0) }
    }

    @Test
    fun claimStartsAnAttemptAndStampsFirstSend() {
        for (state in listOf(OutboxState.PENDING, OutboxState.RETRY_WAIT, OutboxState.UNCERTAIN)) {
            val claimed =
                OutboxMachine.transition(
                    textRecord(state = state, attempts = 1),
                    OutboxEvent.Claim,
                    now,
                    midJitter,
                )
            assertEquals(OutboxState.UPLOADING, claimed.state)
            assertEquals(2, claimed.attempts)
            assertEquals(now, claimed.firstSentAtMs)
            assertEquals(now, claimed.updatedAtMs)
        }
        val again = textRecord(state = OutboxState.RETRY_WAIT, attempts = 1, firstSentAtMs = 5L)
        assertEquals(5L, OutboxMachine.transition(again, OutboxEvent.Claim, now, midJitter).firstSentAtMs)
    }

    @Test
    fun createdAndReconciledSendTheRecord() {
        val uploading = textRecord(state = OutboxState.UPLOADING, attempts = 1, firstSentAtMs = now)
        val sent = OutboxMachine.transition(uploading, OutboxEvent.Created("e1"), now, midJitter)
        assertEquals(OutboxState.SENT, sent.state)
        assertEquals("e1", sent.serverEntryId)
        assertNull(sent.lastError)

        val uncertain = textRecord(state = OutboxState.UNCERTAIN, attempts = 1, firstSentAtMs = now)
        val reconciled = OutboxMachine.transition(uncertain, OutboxEvent.Reconciled("e2"), now, midJitter)
        assertEquals(OutboxState.SENT, reconciled.state)
        assertEquals("e2", reconciled.serverEntryId)
    }

    @Test
    fun failuresMoveByClassAndKeepTheError() {
        val uploading = textRecord(state = OutboxState.UPLOADING, attempts = 1, firstSentAtMs = now)

        val retry = OutboxMachine.transition(uploading, OutboxEvent.Failed(httpError(500)), now, midJitter)
        assertEquals(OutboxState.RETRY_WAIT, retry.state)
        assertEquals(now + 30_000L, retry.nextAttemptAtMs)
        assertEquals(500, retry.lastError?.status)
        assertEquals(now, retry.lastError?.atMs)

        val auth = OutboxMachine.transition(uploading, OutboxEvent.Failed(httpError(401)), now, midJitter)
        assertEquals(OutboxState.RETRY_WAIT, auth.state)

        val maybe = networkError(Delivery.MAYBE_SENT, ClientCodes.TIMEOUT)
        val uncertain = OutboxMachine.transition(uploading, OutboxEvent.Failed(maybe), now, midJitter)
        assertEquals(OutboxState.UNCERTAIN, uncertain.state)
        assertEquals(now + 30_000L, uncertain.nextAttemptAtMs)
        val lowJitter = OutboxMachine.transition(uploading, OutboxEvent.Failed(maybe), now, 0.0)
        assertTrue(lowJitter.nextAttemptAtMs - now >= OutboxMachine.RECONCILE_DELAY_MS)

        val permanent =
            OutboxMachine.transition(
                uploading,
                OutboxEvent.Failed(httpError(422, "VALIDATION_ERROR")),
                now,
                midJitter,
            )
        assertEquals(OutboxState.FAILED, permanent.state)
        assertEquals("VALIDATION_ERROR", permanent.lastError?.code)
    }

    @Test
    fun reconcileFailureStaysUncertainWithBackoff() {
        val uncertain = textRecord(state = OutboxState.UNCERTAIN, attempts = 2, firstSentAtMs = 1L)
        val error = networkError(Delivery.NOT_SENT, ClientCodes.HOST_UNREACHABLE)
        val next = OutboxMachine.transition(uncertain, OutboxEvent.ReconcileFailed(error), now, midJitter)
        assertEquals(OutboxState.UNCERTAIN, next.state)
        assertEquals(now + 60_000L, next.nextAttemptAtMs)
        assertEquals(ClientCodes.HOST_UNREACHABLE, next.lastError?.code)
    }

    @Test
    fun interruptedBecomesUncertainAfterTheReconcileDelay() {
        val uploading = textRecord(state = OutboxState.UPLOADING, attempts = 1, firstSentAtMs = now)
        val next = OutboxMachine.transition(uploading, OutboxEvent.Interrupted, now, midJitter)
        assertEquals(OutboxState.UNCERTAIN, next.state)
        assertEquals(now + OutboxMachine.RECONCILE_DELAY_MS, next.nextAttemptAtMs)
        assertEquals(OutboxCodes.UPLOAD_INTERRUPTED, next.lastError?.code)
    }

    @Test
    fun userRetryMakesDueNowButKeepsUncertainUncertain() {
        for (state in listOf(OutboxState.FAILED, OutboxState.RETRY_WAIT, OutboxState.PENDING)) {
            val next =
                OutboxMachine.transition(
                    textRecord(state = state, attempts = 1, nextAttemptAtMs = now + 5),
                    OutboxEvent.UserRetry,
                    now,
                    midJitter,
                )
            assertEquals(OutboxState.PENDING, next.state)
            assertEquals(0L, next.nextAttemptAtMs)
        }
        val uncertain =
            textRecord(
                state = OutboxState.UNCERTAIN,
                attempts = 1,
                firstSentAtMs = 1L,
                nextAttemptAtMs =
                    now + 5,
            )
        val next = OutboxMachine.transition(uncertain, OutboxEvent.UserRetry, now, midJitter)
        assertEquals(OutboxState.UNCERTAIN, next.state)
        assertTrue(OutboxMachine.isDue(next, now))
    }

    @Test
    fun unsendableFailsWithoutAnAttempt() {
        val event =
            OutboxEvent.Unsendable(
                OutboxCodes.RECORDING_MISSING,
                "Recording is gone.",
                "Record again.",
                emptyList(),
            )
        val next = OutboxMachine.transition(textRecord(), event, now, midJitter)
        assertEquals(OutboxState.FAILED, next.state)
        assertEquals(0, next.attempts)
        assertEquals(OutboxCodes.RECORDING_MISSING, next.lastError?.code)
    }

    @Test
    fun illegalPairsThrowNamingStateAndEvent() {
        val illegal: List<Pair<OutboxState, OutboxEvent>> =
            listOf(
                OutboxState.UPLOADING to OutboxEvent.Claim,
                OutboxState.FAILED to OutboxEvent.Claim,
                OutboxState.PENDING to OutboxEvent.Created("e"),
                OutboxState.RETRY_WAIT to OutboxEvent.Failed(httpError(500)),
                OutboxState.UPLOADING to OutboxEvent.Reconciled("e"),
                OutboxState.PENDING to OutboxEvent.ReconcileFailed(httpError(500)),
                OutboxState.UNCERTAIN to OutboxEvent.Interrupted,
                OutboxState.UPLOADING to OutboxEvent.UserRetry,
                OutboxState.UPLOADING to OutboxEvent.Unsendable(OutboxCodes.RECORDING_MISSING, "m", null, emptyList()),
            )
        for ((state, event) in illegal) {
            val record = textRecord(state = state, attempts = 1, firstSentAtMs = 1L)
            val error =
                assertFailsWith<IllegalStateException> { OutboxMachine.transition(record, event, now, midJitter) }
            assertTrue(error.message!!.contains(state.name), error.message)
            assertTrue(error.message!!.contains(event::class.simpleName!!), error.message)
        }
        val sent = textRecord(state = OutboxState.SENT, attempts = 1, serverEntryId = "e")
        for (event in listOf(OutboxEvent.Claim, OutboxEvent.UserRetry, OutboxEvent.Interrupted)) {
            assertFailsWith<IllegalStateException> { OutboxMachine.transition(sent, event, now, midJitter) }
        }
    }

    @Test
    fun dueness() {
        assertTrue(OutboxMachine.isDue(textRecord(), now))
        val waiting = textRecord(state = OutboxState.RETRY_WAIT, attempts = 1, nextAttemptAtMs = now)
        assertTrue(OutboxMachine.isDue(waiting, now))
        assertFalse(OutboxMachine.isDue(waiting, now - 1))
        val uncertain =
            textRecord(state = OutboxState.UNCERTAIN, attempts = 1, firstSentAtMs = 1L, nextAttemptAtMs = now)
        assertTrue(OutboxMachine.isDue(uncertain, now))
        assertFalse(OutboxMachine.isDue(uncertain, now - 1))
        assertFalse(OutboxMachine.isDue(textRecord(state = OutboxState.UPLOADING, attempts = 1), Long.MAX_VALUE))
        assertFalse(OutboxMachine.isDue(textRecord(state = OutboxState.FAILED, attempts = 1), Long.MAX_VALUE))
        assertFalse(
            OutboxMachine.isDue(
                textRecord(state = OutboxState.SENT, attempts = 1, serverEntryId = "e"),
                Long.MAX_VALUE,
            ),
        )
    }
}
