package dev.msj.journal.core.outbox

import dev.msj.journal.core.api.Artifact
import dev.msj.journal.core.api.ArtifactKind
import dev.msj.journal.core.api.EntryDetail
import dev.msj.journal.core.api.EntrySource
import java.time.Instant
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class ReconcilerTest {
    private val sentAt = Instant.parse("2026-11-08T14:00:00Z").toEpochMilli()
    private val sha = "a".repeat(64)

    private val uncertainText = textRecord(state = OutboxState.UNCERTAIN, attempts = 1, firstSentAtMs = sentAt)

    private val uncertainAudio =
        uncertainText.copy(
            payload =
                OutboxPayload.Audio(
                    path = "/data/outbox/rec-1.ogg",
                    mimeType = "audio/ogg",
                    fileName = "rec-1.ogg",
                    sha256 = sha,
                    sizeBytes = 10,
                    durationMs = 1_000,
                ),
        )

    private fun entry(
        id: String = "e1",
        createdAt: String = "2026-11-08T14:00:02.123456+00:00",
        source: EntrySource = EntrySource.TEXT,
        artifactKind: String = ArtifactKind.USER_TEXT,
        content: String = "Walked by the river.",
        title: String? = "Morning",
        tags: List<String> = listOf("walk", "river"),
        entryDate: String = "2026-11-08",
    ): EntryDetail =
        EntryDetail(
            id = id,
            entryDate = entryDate,
            createdAt = createdAt,
            title = title,
            tags = tags,
            source = source,
            revision = 1,
            artifacts =
                listOf(
                    Artifact(id = "a-$id", kind = artifactKind, content = content, createdAt = createdAt, revision = 1),
                ),
        )

    private fun audioEntry(
        id: String = "e1",
        fileName: String = "$sha.ogg",
    ): EntryDetail =
        entry(
            id = id,
            source = EntrySource.AUDIO,
            artifactKind = ArtifactKind.RAW_AUDIO,
            content = "audio/$id/$fileName",
        )

    @Test
    fun textMatchesOnlyTheExactText() {
        assertEquals("e1", Reconciler.findMatch(uncertainText, listOf(entry()), emptySet()))
        assertNull(Reconciler.findMatch(uncertainText, listOf(entry(content = "Walked by the river. ")), emptySet()))
        assertNull(
            Reconciler.findMatch(uncertainText, listOf(entry(artifactKind = ArtifactKind.RAW_TRANSCRIPT)), emptySet()),
        )
    }

    @Test
    fun audioMatchesTheStoredFileNamedAfterTheHash() {
        assertEquals("e1", Reconciler.findMatch(uncertainAudio, listOf(audioEntry()), emptySet()))
        assertNull(
            Reconciler.findMatch(uncertainAudio, listOf(audioEntry(fileName = "${"b".repeat(64)}.ogg")), emptySet()),
        )
        assertNull(Reconciler.findMatch(uncertainAudio, listOf(audioEntry(fileName = "x$sha.ogg")), emptySet()))
        // A text entry whose text happens to be the hash is not the recording.
        assertNull(Reconciler.findMatch(uncertainAudio, listOf(entry(content = "$sha.ogg")), emptySet()))
    }

    @Test
    fun metadataMustMatch() {
        assertNull(Reconciler.findMatch(uncertainText, listOf(entry(title = "Evening")), emptySet()))
        assertNull(Reconciler.findMatch(uncertainText, listOf(entry(title = null)), emptySet()))
        assertNull(Reconciler.findMatch(uncertainText, listOf(entry(tags = listOf("river", "walk"))), emptySet()))
        assertNull(Reconciler.findMatch(uncertainText, listOf(entry(entryDate = "2026-11-09")), emptySet()))
        val untitled = uncertainText.copy(title = null)
        assertEquals("e1", Reconciler.findMatch(untitled, listOf(entry(title = null)), emptySet()))
    }

    @Test
    fun claimedEntriesAreExcluded() {
        val candidates = listOf(entry(id = "e1"), entry(id = "e2", createdAt = "2026-11-08T14:00:05Z"))
        assertEquals("e2", Reconciler.findMatch(uncertainText, candidates, setOf("e1")))
        assertNull(Reconciler.findMatch(uncertainText, candidates, setOf("e1", "e2")))
    }

    @Test
    fun creationTimeBoundsTheMatch() {
        // Within the clock-skew tolerance before the first send.
        assertEquals(
            "e1",
            Reconciler.findMatch(uncertainText, listOf(entry(createdAt = "2026-11-08T13:51:00Z")), emptySet()),
        )
        assertNull(Reconciler.findMatch(uncertainText, listOf(entry(createdAt = "2026-11-08T13:49:59Z")), emptySet()))
        assertNull(Reconciler.findMatch(uncertainText, listOf(entry(createdAt = "yesterday")), emptySet()))
        assertNull(Reconciler.findMatch(uncertainText.copy(firstSentAtMs = null), listOf(entry()), emptySet()))
    }

    @Test
    fun severalMatchesPickTheEarliestThenTheSmallestId() {
        val candidates =
            listOf(
                entry(id = "e3", createdAt = "2026-11-08T14:00:09Z"),
                entry(id = "e2", createdAt = "2026-11-08T14:00:01Z"),
                entry(id = "e1", createdAt = "2026-11-08T14:00:01Z"),
            )
        assertEquals("e1", Reconciler.findMatch(uncertainText, candidates, emptySet()))
    }
}
