package dev.msj.journal.ui.components

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.msj.journal.ui.UserError
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.JournalShapes
import dev.msj.journal.ui.theme.Space

/** How loud a notice is: errors use the failed color, warnings the attention color. */
enum class NoticeTone { ERROR, WARNING }

/**
 * An error, stated plainly: the message and its stable code first, then what to
 * do next, with the labelled details behind "Show details". No apology.
 */
@Composable
fun ErrorNotice(
    error: UserError,
    modifier: Modifier = Modifier,
    tone: NoticeTone = NoticeTone.ERROR,
    actions: @Composable () -> Unit = {},
) {
    val c = Journal.colors
    val (edge, tint, marker) =
        when (tone) {
            NoticeTone.ERROR -> Triple(c.stFailed, c.stFailedTint, MarkerKind.FAILED)
            NoticeTone.WARNING -> Triple(c.stAttention, c.stAttentionTint, MarkerKind.ATTENTION)
        }
    var showDetails by rememberSaveable(error) { mutableStateOf(false) }
    Column(
        modifier
            .fillMaxWidth()
            .semantics { errorCode = error.code }
            .background(tint, JournalShapes.control)
            .border(Space.hairline, edge, JournalShapes.control)
            .padding(Space.s3)
            .animateContentSize(),
        verticalArrangement = Arrangement.spacedBy(Space.s2),
    ) {
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(Space.s2)) {
            StatusMarker(marker, label = null, modifier = Modifier.padding(top = 4.dp), size = 13.dp)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Space.s1)) {
                Text(error.message, style = Journal.type.ui, color = c.ink)
                Text(
                    error.code,
                    style = Journal.type.monoSmall,
                    color = edge,
                    modifier =
                        Modifier
                            .testTag(TestTags.ERROR_CODE)
                            .border(Space.hairline, edge, JournalShapes.small)
                            .padding(horizontal = 5.dp, vertical = 1.dp),
                )
                if (error.hint != null) {
                    Text(error.hint, style = Journal.type.uiSmall, color = c.ink2)
                }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (error.details.isNotEmpty()) {
                InlineAction(
                    text = if (showDetails) "Hide details" else "Show details",
                    onClick = { showDetails = !showDetails },
                    modifier = Modifier.testTag(TestTags.ERROR_DETAILS_TOGGLE),
                )
            }
            actions()
        }
        if (showDetails) {
            SelectionContainer {
                Column(
                    Modifier
                        .fillMaxWidth()
                        .background(c.field, JournalShapes.small)
                        .padding(Space.s2),
                    verticalArrangement = Arrangement.spacedBy(Space.s1),
                ) {
                    error.details.forEach { line ->
                        Text(line.label, style = Journal.type.label, color = c.ink3)
                        Text(line.value, style = Journal.type.monoSmall, color = c.ink)
                    }
                }
            }
        }
    }
}
