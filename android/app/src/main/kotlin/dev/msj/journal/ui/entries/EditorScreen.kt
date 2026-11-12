package dev.msj.journal.ui.entries

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import dev.msj.journal.AppServices
import dev.msj.journal.core.api.ApiException
import dev.msj.journal.core.api.EntryDetail
import dev.msj.journal.core.api.EntryPatch
import dev.msj.journal.core.drafts.DraftField
import dev.msj.journal.core.drafts.DraftFields
import dev.msj.journal.core.drafts.changedFields
import dev.msj.journal.core.drafts.differingFields
import dev.msj.journal.core.drafts.reapply
import dev.msj.journal.ui.UserError
import dev.msj.journal.ui.capture.normalizeTitle
import dev.msj.journal.ui.capture.parseTags
import dev.msj.journal.ui.components.ConfirmDialog
import dev.msj.journal.ui.components.ErrorNotice
import dev.msj.journal.ui.components.IconAction
import dev.msj.journal.ui.components.InlineAction
import dev.msj.journal.ui.components.JournalDatePicker
import dev.msj.journal.ui.components.JournalField
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.NoticeTone
import dev.msj.journal.ui.components.PrimaryButton
import dev.msj.journal.ui.components.SectionLabel
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.components.TopBar
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.Space
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** The editable fields as typed (tags as one comma-separated line). */
data class EditValues(
    val title: String = "",
    val tags: String = "",
    val date: String = "",
    val text: String = "",
) {
    fun toDraft(): DraftFields = DraftFields(title = title.trim(), tags = parseTags(tags), date = date, text = text)

    companion object {
        fun of(draft: DraftFields): EditValues =
            EditValues(draft.title, draft.tags.joinToString(", "), draft.date, draft.text)
    }
}

data class EditorUi(
    val loading: Boolean = true,
    val base: EntryDetail? = null,
    val values: EditValues = EditValues(),
    val saving: Boolean = false,
    val saved: Boolean = false,
    val error: UserError? = null,
    /** The entry changed on the server while editing: the server's copy and which fields differ. */
    val conflict: Pair<EntryDetail, List<DraftField>>? = null,
) {
    val dirty: Boolean get() = base != null && !saved && differingFields(fieldsOf(base), values.toDraft()).isNotEmpty()
}

fun fieldsOf(entry: EntryDetail): DraftFields =
    DraftFields(title = entry.title.orEmpty(), tags = entry.tags, date = entry.entryDate, text = entry.text)

/**
 * Edits an entry with the version precondition: the PATCH carries
 * `expected_version`; a 409 ENTRY_CHANGED saves nothing and offers to reload
 * the server's copy and reapply the user's changes on top (web EntryEditor).
 */
class EditorViewModel(
    private val services: AppServices,
    private val id: String,
) : ViewModel() {
    private val mutableUi = MutableStateFlow(EditorUi())
    val ui: StateFlow<EditorUi> = mutableUi.asStateFlow()

    init {
        viewModelScope.launch {
            val api = services.session.current() ?: return@launch
            try {
                val entry = api.getEntry(id)
                mutableUi.value = EditorUi(loading = false, base = entry, values = EditValues.of(fieldsOf(entry)))
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: ApiException) {
                mutableUi.value = EditorUi(loading = false, error = UserError.of(error.error))
            }
        }
    }

    fun edit(change: (EditValues) -> EditValues) =
        mutableUi.update {
            if (it.saving ||
                it.saved
            ) {
                it
            } else {
                it.copy(values = change(it.values))
            }
        }

    fun save() {
        val current = mutableUi.value
        val base = current.base ?: return
        if (current.saving) return
        val changes = changedFields(fieldsOf(base), current.values.toDraft())
        if (changes.isEmpty) {
            mutableUi.update { it.copy(saved = true) }
            return
        }
        val patch =
            EntryPatch(
                title = changes.title?.let { EntryPatch.Field.Set(normalizeTitle(it)) } ?: EntryPatch.Field.Unset,
                tags = changes.tags?.let { EntryPatch.Field.Set(it) } ?: EntryPatch.Field.Unset,
                entryDate = changes.date?.let { EntryPatch.Field.Set(it) } ?: EntryPatch.Field.Unset,
                text = changes.text?.let { EntryPatch.Field.Set(it) } ?: EntryPatch.Field.Unset,
                expectedVersion = base.version,
            )
        mutableUi.update { it.copy(saving = true, error = null) }
        viewModelScope.launch {
            val api = services.session.current() ?: return@launch
            try {
                api.patchEntry(id, patch)
                mutableUi.update { it.copy(saving = false, saved = true) }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: ApiException) {
                if (error.error.code != "ENTRY_CHANGED") {
                    mutableUi.update { it.copy(saving = false, error = UserError.of(error.error)) }
                    return@launch
                }
                try {
                    val server = api.getEntry(id)
                    mutableUi.update {
                        it.copy(saving = false, conflict = server to differingFields(fieldsOf(base), fieldsOf(server)))
                    }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (reload: ApiException) {
                    mutableUi.update { it.copy(saving = false, error = UserError.of(reload.error)) }
                }
            }
        }
    }

    /** Takes the server's copy as the new base and keeps only what the user changed. */
    fun reloadAndReapply() =
        mutableUi.update { state ->
            val base = state.base ?: return@update state
            val (server, _) = state.conflict ?: return@update state
            val next = reapply(fieldsOf(base), fieldsOf(server), state.values.toDraft())
            state.copy(base = server, values = EditValues.of(next), conflict = null)
        }
}

@Composable
fun EditorScreen(
    viewModel: EditorViewModel,
    onClose: (saved: Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    var confirmDiscard by rememberSaveable { mutableStateOf(false) }
    var pickDate by rememberSaveable { mutableStateOf(false) }
    val close = { if (ui.dirty) confirmDiscard = true else onClose(false) }
    BackHandler(onBack = close)
    val latestOnClose by rememberUpdatedState(onClose)
    LaunchedEffect(ui.saved) { if (ui.saved) latestOnClose(true) }
    val c = Journal.colors

    Column(modifier.fillMaxSize().testTag(TestTags.EDITOR_SCREEN)) {
        TopBar(
            title = "Edit entry",
            navigation = { IconAction(JournalIcons.Close, "Close without saving", close) },
            actions = {
                PrimaryButton(
                    "Save",
                    viewModel::save,
                    enabled = ui.base != null && !ui.saving && ui.conflict == null,
                    modifier = Modifier.padding(end = Space.s2).testTag(TestTags.EDITOR_SAVE),
                )
            },
        )
        Column(
            Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(Space.gutter),
            verticalArrangement = Arrangement.spacedBy(Space.s4),
        ) {
            ui.conflict?.let { (server, changed) ->
                ErrorNotice(
                    UserError(
                        code = "ENTRY_CHANGED",
                        message =
                            "This entry changed while you were editing" +
                                (
                                    if (changed.isEmpty()) {
                                        ""
                                    } else {
                                        ": its ${changed.joinToString(
                                            ", ",
                                        ) { it.name.lowercase() }} changed"
                                    }
                                ) +
                                (
                                    ui.base?.version?.let { from ->
                                        server.version?.let { to -> " (version $from to $to)" }
                                    }
                                        ?: ""
                                ) +
                                ". Nothing was saved.",
                        hint = "Reload the entry and reapply your changes, then save again.",
                        details = emptyList(),
                    ),
                    tone = NoticeTone.WARNING,
                    modifier = Modifier.testTag(TestTags.EDITOR_CONFLICT),
                ) {
                    InlineAction(
                        "Reload and reapply my changes",
                        viewModel::reloadAndReapply,
                        modifier = Modifier.testTag(TestTags.EDITOR_REAPPLY),
                    )
                }
            }
            ui.error?.let { ErrorNotice(it) }
            if (ui.base == null) {
                if (ui.loading) Text("Loading the entry", style = Journal.type.ui, color = c.ink3)
                return@Column
            }
            JournalField(
                value = ui.values.text,
                onValueChange = { value -> viewModel.edit { it.copy(text = value) } },
                label = "Text",
                singleLine = false,
                minLines = 8,
                textStyle = Journal.type.prose,
                supporting = "Changing the text makes the server process the entry again.",
                modifier = Modifier.testTag(TestTags.EDITOR_TEXT),
            )
            JournalField(
                value = ui.values.title,
                onValueChange = { value -> viewModel.edit { it.copy(title = value) } },
                label = "Title (optional)",
                modifier = Modifier.testTag(TestTags.EDITOR_TITLE),
            )
            JournalField(
                value = ui.values.tags,
                onValueChange = { value -> viewModel.edit { it.copy(tags = value) } },
                label = "Tags (optional)",
                supporting = "Separate tags with commas.",
            )
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(Space.s3),
            ) {
                Column(Modifier.weight(1f)) {
                    SectionLabel("Journal date")
                    Text(ui.values.date, style = Journal.type.mono, color = c.ink)
                }
                InlineAction("Change date", onClick = { pickDate = true })
            }
        }
    }
    if (confirmDiscard) {
        ConfirmDialog(
            title = "Discard your changes?",
            text = "The entry stays as it is on the server.",
            action = "Discard changes",
            danger = true,
            onConfirm = {
                confirmDiscard = false
                onClose(false)
            },
            onDismiss = { confirmDiscard = false },
        )
    }
    if (pickDate) {
        JournalDatePicker(
            date = ui.values.date,
            today = java.time.LocalDate.now(),
            onPick = { value -> viewModel.edit { it.copy(date = value) } },
            onDismiss = { pickDate = false },
        )
    }
}
