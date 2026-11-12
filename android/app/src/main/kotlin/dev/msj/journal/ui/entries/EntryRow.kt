package dev.msj.journal.ui.entries

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.msj.journal.core.api.EntrySource
import dev.msj.journal.core.api.EntrySummary
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.StatusMarker
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.components.bottomRule
import dev.msj.journal.ui.components.entryId
import dev.msj.journal.ui.components.labelFor
import dev.msj.journal.ui.components.markerFor
import dev.msj.journal.ui.format.headline
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.Space

/**
 * One line per entry on ruled paper: the state marker and the journal date in
 * the margin column, a margin rule, then the headline in the journal's serif.
 * An optional [snippet] (search) adds a second, smaller line.
 */
@Composable
fun EntryRow(
    entry: EntrySummary,
    onOpen: () -> Unit,
    modifier: Modifier = Modifier,
    snippet: String? = null,
    tag: String = TestTags.ENTRY_ROW_PREFIX + entry.id,
) {
    val c = Journal.colors
    Row(
        modifier
            .fillMaxWidth()
            .heightIn(min = Space.rowHeight)
            .clickable(onClickLabel = "Open entry", onClick = onOpen)
            .bottomRule(c.rule)
            .testTag(tag)
            .semantics(mergeDescendants = true) { entryId = entry.id },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Row(
            Modifier.width(Space.gutter + 104.dp).padding(start = Space.gutter),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Space.s2),
        ) {
            StatusMarker(markerFor(entry.status), label = labelFor(entry.status))
            Text(entry.entryDate, style = Journal.type.mono, color = c.ink2, maxLines = 1)
        }
        Box(Modifier.width(Space.hairline).heightIn(min = Space.rowHeight).fillMaxHeight().background(c.margin))
        Column(Modifier.weight(1f).padding(horizontal = Space.s3, vertical = Space.s2)) {
            Text(
                headline(entry).ifEmpty { "(no text yet)" },
                style = Journal.type.rowText,
                color = if (headline(entry).isEmpty()) c.ink3 else c.ink,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            if (!snippet.isNullOrBlank()) {
                Text(
                    snippet,
                    style = Journal.type.uiSmall,
                    color = c.ink2,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
        if (entry.source == EntrySource.AUDIO) {
            Icon(
                JournalIcons.Audio,
                contentDescription = "Voice entry",
                tint = c.ink3,
                modifier = Modifier.padding(end = Space.gutter).size(18.dp),
            )
        } else {
            Box(Modifier.width(Space.gutter))
        }
    }
}
