package dev.msj.journal.ui.capture

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.msj.journal.AppServices
import dev.msj.journal.core.dates.isoDate
import dev.msj.journal.core.dates.parseIsoDate
import dev.msj.journal.ui.UserError
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ComposerUi(
    val text: String = "",
    val title: String = "",
    val tags: String = "",
    /** `YYYY-MM-DD`; today unless the user picks another day. */
    val date: String,
    val saving: Boolean = false,
    val saved: Boolean = false,
    val error: UserError? = null,
) {
    val canSave: Boolean get() = text.isNotBlank() && !saving && !saved
    val dirty: Boolean get() = !saved && (text.isNotBlank() || title.isNotBlank() || tags.isNotBlank())
}

/**
 * A new text entry. It is saved to the outbox on the phone (never straight to
 * the server); the capture id is made once and kept across rotation and
 * process death, so saving twice cannot create two entries.
 */
class ComposerViewModel(
    private val services: AppServices,
    private val handle: SavedStateHandle,
    sharedText: String?,
) : ViewModel() {
    private val captureId: String = handle[KEY_ID] ?: UUID.randomUUID().toString().also { handle[KEY_ID] = it }

    private val mutableUi =
        MutableStateFlow(
            ComposerUi(
                text = handle[KEY_TEXT] ?: sharedText.orEmpty(),
                title = handle[KEY_TITLE] ?: "",
                tags = handle[KEY_TAGS] ?: "",
                date = handle[KEY_DATE] ?: isoDate(services.today()),
            ),
        )
    val ui: StateFlow<ComposerUi> = mutableUi.asStateFlow()

    fun onText(value: String) = change { it.copy(text = value) }

    fun onTitle(value: String) = change { it.copy(title = value) }

    fun onTags(value: String) = change { it.copy(tags = value) }

    fun onDate(value: String) {
        assert(parseIsoDate(value) != null) { "picked date is a calendar date: $value" }
        change { it.copy(date = value) }
    }

    fun save() {
        val current = mutableUi.value
        if (!current.canSave) return
        mutableUi.update { it.copy(saving = true, error = null) }
        viewModelScope.launch {
            try {
                services.outbox.enqueueText(
                    id = captureId,
                    text = current.text,
                    title = normalizeTitle(current.title),
                    tags = parseTags(current.tags),
                    entryDate = current.date,
                )
                mutableUi.update { it.copy(saving = false, saved = true) }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                mutableUi.update { it.copy(saving = false, error = UserError.unexpected("Save the entry on this phone", error)) }
            }
        }
    }

    private fun change(edit: (ComposerUi) -> ComposerUi) {
        if (mutableUi.value.saving || mutableUi.value.saved) return
        mutableUi.update(edit)
        val now = mutableUi.value
        handle[KEY_TEXT] = now.text
        handle[KEY_TITLE] = now.title
        handle[KEY_TAGS] = now.tags
        handle[KEY_DATE] = now.date
    }

    private companion object {
        const val KEY_ID = "capture_id"
        const val KEY_TEXT = "text"
        const val KEY_TITLE = "title"
        const val KEY_TAGS = "tags"
        const val KEY_DATE = "date"
    }
}
