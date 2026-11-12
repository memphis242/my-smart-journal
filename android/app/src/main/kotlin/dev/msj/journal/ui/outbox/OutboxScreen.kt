package dev.msj.journal.ui.outbox

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Icon
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import dev.msj.journal.AppServices
import dev.msj.journal.core.outbox.OutboxPayload
import dev.msj.journal.core.outbox.OutboxRecord
import dev.msj.journal.core.outbox.OutboxState
import dev.msj.journal.ui.UserError
import dev.msj.journal.ui.components.ConfirmDialog
import dev.msj.journal.ui.components.ErrorNotice
import dev.msj.journal.ui.components.IconAction
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.NoticeTone
import dev.msj.journal.ui.components.SecondaryButton
import dev.msj.journal.ui.components.StatusMarker
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.components.TopBar
import dev.msj.journal.ui.components.bottomRule
import dev.msj.journal.ui.components.labelFor
import dev.msj.journal.ui.components.markerFor
import dev.msj.journal.ui.components.stateName
import dev.msj.journal.ui.format.firstLine
import dev.msj.journal.ui.format.formatDuration
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.Space
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class OutboxViewModel(
    private val services: AppServices,
) : ViewModel() {
    val records: StateFlow<List<OutboxRecord>?> =
        services.outbox.unsent.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    private val mutableError = MutableStateFlow<UserError?>(null)
    val error: StateFlow<UserError?> = mutableError.asStateFlow()

    fun retry(id: String) = viewModelScope.launch { services.outbox.retry(id) }

    fun retryAll() = viewModelScope.launch { services.outbox.retryAllNow() }

    fun discard(id: String) =
        viewModelScope.launch {
            val removed = services.outbox.discard(id)
            mutableError.value =
                if (removed) {
                    null
                } else {
                    UserError(
                        code = "UPLOAD_IN_PROGRESS",
                        message = "The entry is being uploaded right now, so it was not discarded.",
                        hint = "Wait for the upload to finish or fail, then discard it if it is still here.",
                        details = emptyList(),
                    )
                }
        }
}

@Composable
fun OutboxScreen(
    viewModel: OutboxViewModel,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val records by viewModel.records.collectAsStateWithLifecycle()
    val error by viewModel.error.collectAsStateWithLifecycle()
    var discarding by rememberSaveable { mutableStateOf<String?>(null) }
    val c = Journal.colors
    val list = records

    Column(modifier.fillMaxSize().testTag(TestTags.OUTBOX_SCREEN)) {
        TopBar(
            title = "Unsent uploads",
            navigation = { IconAction(JournalIcons.Back, "Back", onBack) },
            actions = {
                if (list != null && list.any { it.state != OutboxState.UPLOADING }) {
                    IconAction(JournalIcons.Retry, "Retry every waiting upload now", viewModel::retryAll)
                }
            },
        )
        error?.let { ErrorNotice(it, tone = NoticeTone.WARNING, modifier = Modifier.padding(Space.gutter)) }
        when {
            list == null -> Unit
            list.isEmpty() ->
                Text(
                    "Nothing is waiting. New entries are listed here until the server has them.",
                    style = Journal.type.ui,
                    color = c.ink3,
                    modifier = Modifier.padding(Space.gutter).testTag(TestTags.OUTBOX_EMPTY),
                )
            else ->
                LazyColumn(Modifier.weight(1f).fillMaxWidth()) {
                    items(list, key = { it.id }) { record ->
                        OutboxRow(record, onRetry = { viewModel.retry(record.id) }, onDiscard = { discarding = record.id })
                    }
                }
        }
        Text(
            "Entries are kept on this phone until the server confirms them. Uploads retry on their own when the server is reachable.",
            style = Journal.type.uiSmall,
            color = c.ink3,
            modifier = Modifier.windowInsetsPadding(WindowInsets.navigationBars).padding(Space.gutter),
        )
    }

    discarding?.let { id ->
        ConfirmDialog(
            title = "Discard this entry?",
            text = "It has not reached the server. Discarding deletes it from this phone for good.",
            action = "Discard",
            danger = true,
            onConfirm = {
                discarding = null
                viewModel.discard(id)
            },
            onDismiss = { discarding = null },
        )
    }
}

private val CAPTURED = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm", Locale.ROOT)

@Composable
private fun OutboxRow(
    record: OutboxRecord,
    onRetry: () -> Unit,
    onDiscard: () -> Unit,
) {
    val c = Journal.colors
    val uploading = record.state == OutboxState.UPLOADING
    Column(
        Modifier
            .fillMaxWidth()
            .bottomRule(c.rule)
            .padding(horizontal = Space.gutter, vertical = Space.s3)
            .testTag(TestTags.OUTBOX_ROW_PREFIX + record.id)
            .semantics { stateName = record.state.name },
        verticalArrangement = Arrangement.spacedBy(Space.s2),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.s2)) {
            StatusMarker(markerFor(record.state), label = null)
            Icon(
                if (record.payload is OutboxPayload.Audio) JournalIcons.Audio else JournalIcons.Text,
                contentDescription = if (record.payload is OutboxPayload.Audio) "Voice entry" else "Written entry",
                tint = c.ink3,
                modifier = Modifier.size(16.dp),
            )
            Text(record.entryDate, style = Journal.type.mono, color = c.ink2)
            Text(
                preview(record),
                style = Journal.type.rowText,
                color = c.ink,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
        }
        Text(
            buildString {
                append(labelFor(record.state))
                append("  ·  saved ")
                append(Instant.ofEpochMilli(record.createdAtMs).atZone(ZoneId.systemDefault()).format(CAPTURED))
                if (record.attempts > 0) append("  ·  ").append(record.attempts).append(if (record.attempts == 1) " attempt" else " attempts")
            },
            style = Journal.type.monoSmall,
            color = c.ink3,
        )
        record.lastError?.let { failure ->
            ErrorNotice(
                UserError.of(failure),
                tone = if (record.state == OutboxState.FAILED) NoticeTone.ERROR else NoticeTone.WARNING,
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(Space.s2)) {
            SecondaryButton(
                "Retry now",
                onRetry,
                enabled = !uploading,
                icon = JournalIcons.Retry,
                modifier = Modifier.testTag(TestTags.OUTBOX_RETRY_PREFIX + record.id),
            )
            SecondaryButton(
                "Discard",
                onDiscard,
                enabled = !uploading,
                danger = true,
                icon = JournalIcons.Trash,
                modifier = Modifier.testTag(TestTags.OUTBOX_DISCARD_PREFIX + record.id),
            )
        }
    }
}

private fun preview(record: OutboxRecord): String {
    record.title?.let { return it }
    return when (val payload = record.payload) {
        is OutboxPayload.Text -> firstLine(payload.text)
        is OutboxPayload.Audio -> "Recording ${formatDuration(payload.durationMs)}"
    }
}
