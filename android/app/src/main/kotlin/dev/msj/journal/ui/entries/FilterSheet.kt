package dev.msj.journal.ui.entries

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import dev.msj.journal.core.api.FacetValue
import dev.msj.journal.ui.components.ErrorNotice
import dev.msj.journal.ui.components.IconAction
import dev.msj.journal.ui.components.InlineAction
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.PrimaryButton
import dev.msj.journal.ui.components.SectionLabel
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.JournalShapes
import dev.msj.journal.ui.theme.Space

/**
 * The filter control: muted until a filter is set, then accent-colored with
 * the number of selected values.
 */
@Composable
fun FilterAction(
    selected: Int,
    onOpen: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val c = Journal.colors
    Box(modifier, contentAlignment = Alignment.TopEnd) {
        IconAction(
            JournalIcons.Filter,
            label = if (selected == 0) "Filter by tag, project or skill" else "$selected filter values set",
            onClick = onOpen,
            tint = if (selected == 0) c.ink2 else c.accent,
            modifier = Modifier.testTag(TestTags.FILTER_OPEN),
        )
        if (selected > 0) {
            Text(
                "$selected",
                style = Journal.type.monoSmall,
                color = c.accentInk,
                modifier =
                    Modifier
                        .padding(top = 4.dp, end = 2.dp)
                        .clip(CircleShape)
                        .background(c.accent)
                        .padding(horizontal = 5.dp),
            )
        }
    }
}

/** Facet values with their counts; a tap adds or removes a value. Values of one kind are ORed, kinds ANDed. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun FilterSheet(
    facets: FacetsState,
    selection: FacetSelection,
    onToggle: (FacetKind, String) -> Unit,
    onClear: () -> Unit,
    onDismiss: () -> Unit,
) {
    val c = Journal.colors
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = c.surfaceRaised,
        shape = JournalShapes.sheet,
        modifier = Modifier.testTag(TestTags.FILTER_SHEET),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = Space.gutter)
                .padding(bottom = Space.s6),
            verticalArrangement = Arrangement.spacedBy(Space.s4),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Filter entries", style = Journal.type.barTitle, color = c.ink, modifier = Modifier.weight(1f))
                if (selection.size > 0) {
                    InlineAction("Clear filters", onClick = onClear, modifier = Modifier.testTag(TestTags.FILTER_CLEAR))
                }
            }
            facets.error?.let { ErrorNotice(it) }
            val loaded = facets.facets
            if (loaded == null) {
                Text(
                    if (facets.loading) "Loading the values" else "No values loaded.",
                    style = Journal.type.ui,
                    color = c.ink3,
                )
            } else {
                Group("Tags", FacetKind.TAG, loaded.tags, selection, onToggle)
                Group("Projects", FacetKind.PROJECT, loaded.projects, selection, onToggle)
                Group("Skills", FacetKind.SKILL, loaded.skills, selection, onToggle)
                Text(
                    "Several values of one kind match any of them; values of different kinds must all match.",
                    style = Journal.type.uiSmall,
                    color = c.ink3,
                )
            }
            PrimaryButton("Done", onClick = onDismiss, modifier = Modifier.align(Alignment.End))
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Group(
    label: String,
    kind: FacetKind,
    values: List<FacetValue>,
    selection: FacetSelection,
    onToggle: (FacetKind, String) -> Unit,
) {
    val c = Journal.colors
    Column(verticalArrangement = Arrangement.spacedBy(Space.s2)) {
        SectionLabel(label)
        if (values.isEmpty()) {
            Text("None yet.", style = Journal.type.uiSmall, color = c.ink3)
            return@Column
        }
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(Space.s2),
            verticalArrangement = Arrangement.spacedBy(Space.s2),
        ) {
            values.forEach { facet ->
                val on = selection.has(kind, facet.value)
                Row(
                    Modifier
                        .heightIn(min = 40.dp)
                        .clip(JournalShapes.control)
                        .background(if (on) c.accentTint else c.surface)
                        .border(
                            if (on) 1.5.dp else Space.hairline,
                            if (on) c.accent else c.fieldBorder,
                            JournalShapes.control,
                        ).toggleable(value = on, role = Role.Checkbox, onValueChange = { onToggle(kind, facet.value) })
                        .padding(horizontal = Space.s3)
                        .testTag(TestTags.FILTER_CHIP_PREFIX + kind.name + "." + facet.value),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(Space.s2),
                ) {
                    if (on) {
                        androidx.compose.material3.Icon(
                            JournalIcons.Check,
                            contentDescription = null,
                            tint = c.accent,
                            modifier = Modifier.size(16.dp),
                        )
                    }
                    Text(
                        facet.value,
                        style = if (on) Journal.type.uiStrong else Journal.type.ui,
                        color = if (on) c.accent else c.ink,
                    )
                    Text("${facet.count}", style = Journal.type.monoSmall, color = c.ink3)
                }
            }
        }
    }
}
