package dev.msj.journal.core.outbox

import dev.msj.journal.core.api.ArtifactKind
import dev.msj.journal.core.api.EntryDetail
import dev.msj.journal.core.api.EntrySource
import java.time.OffsetDateTime
import java.time.format.DateTimeParseException

/**
 * Finds the server entry an UNCERTAIN record already created, so it is never
 * created twice. `POST /entries` has no idempotency key, so a match is
 * inferred from what the server stores verbatim at creation: the date, title,
 * tags, source and the revision-1 content artifact (`user_text` with the exact
 * text, or `raw_audio` whose stored file is named after the recording's
 * SHA-256), created no earlier than the first request could have arrived.
 *
 * Residual risk: an entry edited on another device between creation and
 * reconciliation may no longer match (a changed title, tags or date, or text
 * superseded so that the revision-1 artifact is not returned), and the record
 * would then be uploaded a second time. Reconciliation runs within about a
 * minute of the uncertain attempt, so that window is short.
 */
public object Reconciler {
    /** Allowed difference between the phone's clock and the server's when bounding by creation time. */
    public const val CLOCK_SKEW_TOLERANCE_MS: Long = 10L * 60 * 1000

    /**
     * The id of the entry among [candidates] (the server's entries for the
     * record's date) that [record] created, or null when none matches.
     * [claimedEntryIds] are entries other records already own. When several
     * match, the earliest created (then the smallest id) is chosen, so the
     * answer is deterministic.
     */
    public fun findMatch(
        record: OutboxRecord,
        candidates: List<EntryDetail>,
        claimedEntryIds: Set<String>,
    ): String? {
        // A record never sent cannot have created anything.
        val firstSentAtMs = record.firstSentAtMs ?: return null
        val earliestMs = firstSentAtMs - CLOCK_SKEW_TOLERANCE_MS
        return candidates
            .asSequence()
            .filter { it.id !in claimedEntryIds }
            .filter { it.entryDate == record.entryDate && it.title == record.title && it.tags == record.tags }
            .filter { contentMatches(record.payload, it) }
            .mapNotNull { entry -> parseInstantMs(entry.createdAt)?.let { createdMs -> entry to createdMs } }
            .filter { (_, createdMs) -> createdMs >= earliestMs }
            .minWithOrNull(compareBy<Pair<EntryDetail, Long>> { it.second }.thenBy { it.first.id })
            ?.first
            ?.id
    }

    private fun contentMatches(
        payload: OutboxPayload,
        entry: EntryDetail,
    ): Boolean =
        when (payload) {
            is OutboxPayload.Text -> {
                entry.source == EntrySource.TEXT &&
                    entry.artifacts.any { it.kind == ArtifactKind.USER_TEXT && it.content == payload.text }
            }

            is OutboxPayload.Audio -> {
                val prefix = "${payload.sha256}."
                entry.source == EntrySource.AUDIO &&
                    entry.artifacts.any {
                        it.kind == ArtifactKind.RAW_AUDIO && it.content.substringAfterLast('/').startsWith(prefix)
                    }
            }
        }

    /** Epoch milliseconds of an ISO 8601 timestamp with offset, or null when it cannot be parsed. */
    internal fun parseInstantMs(iso: String): Long? =
        try {
            OffsetDateTime.parse(iso).toInstant().toEpochMilli()
        } catch (_: DateTimeParseException) {
            null
        }
}
