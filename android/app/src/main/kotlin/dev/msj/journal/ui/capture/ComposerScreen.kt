package dev.msj.journal.ui.capture

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.msj.journal.ui.components.ConfirmDialog
import dev.msj.journal.ui.components.ErrorNotice
import dev.msj.journal.ui.components.IconAction
import dev.msj.journal.ui.components.InlineAction
import dev.msj.journal.ui.components.JournalDatePicker
import dev.msj.journal.ui.components.JournalField
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.PrimaryButton
import dev.msj.journal.ui.components.SectionLabel
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.components.TopBar
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.Space
import java.time.LocalDate

/**
 * Write a new entry: a full-screen form with an explicit close (X) and Save.
 * Leaving with unsaved text asks first.
 */
@Composable
fun ComposerScreen(
    viewModel: ComposerViewModel,
    today: LocalDate,
    onClose: (saved: Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    var confirmDiscard by rememberSaveable { mutableStateOf(false) }
    var pickDate by rememberSaveable { mutableStateOf(false) }
    val close = { if (ui.dirty) confirmDiscard = true else onClose(false) }
    BackHandler(onBack = close)
    LaunchedEffect(ui.saved) { if (ui.saved) onClose(true) }
    val c = Journal.colors

    Column(modifier.fillMaxSize().testTag(TestTags.COMPOSER_SCREEN)) {
        TopBar(
            title = "New entry",
            navigation = {
                IconAction(JournalIcons.Close, "Close without saving", close, modifier = Modifier.testTag(TestTags.COMPOSER_CLOSE))
            },
            actions = {
                PrimaryButton(
                    text = "Save",
                    onClick = viewModel::save,
                    enabled = ui.canSave,
                    modifier = Modifier.padding(end = Space.s2).testTag(TestTags.COMPOSER_SAVE),
                )
            },
        )
        Column(
            Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(horizontal = Space.gutter, vertical = Space.s4),
            verticalArrangement = Arrangement.spacedBy(Space.s4),
        ) {
            JournalField(
                value = ui.text,
                onValueChange = viewModel::onText,
                label = "Entry",
                placeholder = "What happened, what you worked on, what you learned.",
                singleLine = false,
                minLines = 8,
                textStyle = Journal.type.prose,
                modifier = Modifier.testTag(TestTags.COMPOSER_TEXT),
            )
            JournalField(
                value = ui.title,
                onValueChange = viewModel::onTitle,
                label = "Title (optional)",
                modifier = Modifier.testTag(TestTags.COMPOSER_TITLE),
            )
            JournalField(
                value = ui.tags,
                onValueChange = viewModel::onTags,
                label = "Tags (optional)",
                supporting = "Separate tags with commas.",
                modifier = Modifier.testTag(TestTags.COMPOSER_TAGS),
            )
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.s3)) {
                Column(Modifier.weight(1f)) {
                    SectionLabel("Journal date")
                    Text(ui.date, style = Journal.type.mono, color = c.ink, modifier = Modifier.padding(top = 2.dp))
                }
                InlineAction("Change date", onClick = { pickDate = true })
            }
            Text(
                "Saved on this phone first, then uploaded when the server is reachable.",
                style = Journal.type.uiSmall,
                color = c.ink3,
                modifier = Modifier.fillMaxWidth(),
            )
            ui.error?.let { ErrorNotice(it) }
        }
    }

    if (confirmDiscard) {
        ConfirmDialog(
            title = "Discard this entry?",
            text = "The text you wrote is not saved anywhere yet. Discarding deletes it.",
            action = "Discard",
            danger = true,
            onConfirm = {
                confirmDiscard = false
                onClose(false)
            },
            onDismiss = { confirmDiscard = false },
        )
    }
    if (pickDate) {
        JournalDatePicker(date = ui.date, today = today, onPick = viewModel::onDate, onDismiss = { pickDate = false })
    }
}
