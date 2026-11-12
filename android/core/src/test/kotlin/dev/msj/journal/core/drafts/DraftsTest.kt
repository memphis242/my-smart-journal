package dev.msj.journal.core.drafts

import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class DraftsTest {
    private val base =
        DraftFields(title = "Demo night", tags = listOf("school"), date = "2026-11-07", text = "Demo done.")

    @Test
    fun `differing fields ignore surrounding whitespace in title and text`() {
        assertTrue(differingFields(base, base.copy(title = " Demo night ", text = "Demo done.\n")).isEmpty())
        assertEquals(
            listOf(DraftField.DATE, DraftField.TAGS),
            differingFields(base, base.copy(date = "2026-11-08", tags = listOf("school", "kv"))),
        )
    }

    @Test
    fun `tag order and case are significant`() {
        assertEquals(
            listOf(DraftField.TAGS),
            differingFields(base.copy(tags = listOf("a", "b")), base.copy(tags = listOf("b", "a"))),
        )
    }

    @Test
    fun `changed fields hold only what the user changed`() {
        val changes = changedFields(base, base.copy(title = "Demo night!", text = "Demo done."))
        assertEquals(DraftChanges(title = "Demo night!"), changes)
        assertTrue(changedFields(base, base).isEmpty)
    }

    @Test
    fun `reapply keeps the user's edits and takes the server's values for the rest`() {
        val serverNow = base.copy(text = "Demo done. Edited elsewhere.", tags = listOf("school", "kv"))
        val mine = base.copy(title = "My title")
        val merged = reapply(oldBase = base, newBase = serverNow, values = mine)
        assertEquals(serverNow.copy(title = "My title"), merged)
    }

    @Test
    fun `reapply with a clearing edit keeps the cleared field`() {
        val merged = reapply(base, base.copy(date = "2026-11-09"), base.copy(title = "", tags = emptyList()))
        assertEquals(DraftFields(title = "", tags = emptyList(), date = "2026-11-09", text = "Demo done."), merged)
    }

    @Test
    fun `reapply with no edits is the new base`() {
        val serverNow = base.copy(text = "changed")
        assertEquals(serverNow, reapply(base, serverNow, base))
    }
}
