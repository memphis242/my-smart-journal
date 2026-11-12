package dev.msj.journal.ui

import dev.msj.journal.core.api.EntryFilters
import dev.msj.journal.core.api.EntrySource
import dev.msj.journal.core.api.EntryStatus
import dev.msj.journal.core.api.EntrySummary
import dev.msj.journal.ui.capture.normalizeTitle
import dev.msj.journal.ui.capture.parseTags
import dev.msj.journal.ui.entries.NavContext
import dev.msj.journal.ui.format.entryPlainText
import dev.msj.journal.ui.format.formatDuration
import dev.msj.journal.ui.format.formatSize
import dev.msj.journal.ui.format.headline
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class UiLogicTest {
    private fun entry(
        id: String,
        date: String = "2026-11-08",
        created: String = "2026-11-08T10:00:00Z",
        title: String? = null,
        summary: String? = null,
        text: String = "",
        tags: List<String> = emptyList(),
    ) = EntrySummary(
        id = id,
        entryDate = date,
        createdAt = created,
        title = title,
        tags = tags,
        source = EntrySource.TEXT,
        summary = summary,
        text = text,
        projects = emptyList(),
        skills = emptyList(),
        status = EntryStatus.DONE,
        revision = 1,
        version = 1,
    )

    @Test
    fun tagsAreTrimmedDedupedIgnoringCaseAndBlanksDropped() {
        assertEquals(listOf("work", "Kotlin", "phone"), parseTags(" work, Kotlin ,,WORK, phone ,kotlin "))
        assertEquals(emptyList(), parseTags(" , ,"))
    }

    @Test
    fun blankTitleIsNoTitle() {
        assertNull(normalizeTitle("   "))
        assertEquals("Standup", normalizeTitle("  Standup "))
    }

    @Test
    fun headlinePrefersTitleThenSummaryThenFirstLine() {
        assertEquals("T", headline(entry("a", title = " T ", summary = "S", text = "x")))
        assertEquals("S", headline(entry("a", title = " ", summary = " S ", text = "x")))
        assertEquals("first", headline(entry("a", text = "\n first \nsecond")))
    }

    @Test
    fun plainTextMatchesTheExportLayout() {
        val text = entryPlainText(entry("a", title = "Title", summary = "Sum", text = "Body", tags = listOf("a", "b")))
        assertEquals("2026-11-08  Title\n\nSummary:\nSum\n\nBody\n\nTags:\na, b", text)
        assertEquals("2026-11-08\n\nBody", entryPlainText(entry("a", text = "Body")))
    }

    @Test
    fun durationsAndSizesAreExact() {
        assertEquals("0:00", formatDuration(0))
        assertEquals("1:05", formatDuration(65_400))
        assertEquals("1:00:01", formatDuration(3_601_000))
        assertEquals("812 B", formatSize(812))
        assertEquals("41 KB", formatSize(41 * 1024 + 10))
        assertEquals("3.5 MB", formatSize(3_670_016))
    }

    @Test
    fun localNeighborsFollowTheListOrder() {
        val list = listOf(entry("n"), entry("m"), entry("o"))
        val context = NavContext.fromList(list, EntryFilters.NONE, complete = true)
        assertEquals(NavContext.Local(newer = null, older = "m", index = 0), context.local("n"))
        assertEquals(NavContext.Local(newer = "n", older = "o", index = 1), context.local("m"))
        assertFalse(context.needsServer("o"))
    }

    @Test
    fun incompleteListAsksTheServerAtItsEdges() {
        val context =
            NavContext.fromList(
                listOf(entry("n"), entry("m")),
                EntryFilters(from = "2026-11-01"),
                complete = false,
            )
        assertTrue(context.needsServer("m"))
        assertTrue(context.needsServer("n"))
        assertTrue(context.needsServer("unknown"))
    }

    @Test
    fun searchResultsStepByDateThenCreatedThenId() {
        val ranked =
            listOf(
                entry("b", date = "2026-11-01"),
                entry("c", date = "2026-11-08", created = "2026-11-08T09:00:00Z"),
                entry("a", date = "2026-11-08", created = "2026-11-08T11:00:00Z"),
            )
        assertEquals(listOf("a", "c", "b"), NavContext.chronological(ranked).newestFirst)
    }
}
