package dev.msj.journal.ui.search

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import dev.msj.journal.AppServices
import dev.msj.journal.core.api.ApiException
import dev.msj.journal.core.api.EntryFilters
import dev.msj.journal.core.api.Hints
import dev.msj.journal.core.api.SearchEvent
import dev.msj.journal.core.api.SearchHit
import dev.msj.journal.core.api.Unavailable
import dev.msj.journal.ui.UserError
import dev.msj.journal.ui.components.ErrorNotice
import dev.msj.journal.ui.components.IconAction
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.MarkerKind
import dev.msj.journal.ui.components.NoticeTone
import dev.msj.journal.ui.components.PrimaryButton
import dev.msj.journal.ui.components.SectionLabel
import dev.msj.journal.ui.components.StatusMarker
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.components.TopBar
import dev.msj.journal.ui.components.count
import dev.msj.journal.ui.entries.EntryRow
import dev.msj.journal.ui.entries.NavContext
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.JournalShapes
import dev.msj.journal.ui.theme.Space
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class TierResult(
    val tier: Int,
    val hits: List<SearchHit>,
    val unavailable: Unavailable?,
)

data class SearchUi(
    val query: String = "",
    val submitted: String? = null,
    val tiers: List<TierResult> = emptyList(),
    val running: Boolean = false,
    val done: Boolean = false,
    val elapsedMs: Double? = null,
    val error: UserError? = null,
) {
    val hits: List<SearchHit> get() = tiers.flatMap { it.hits }
}

/** Keyword search over the five tiers, each rendered as soon as its NDJSON line arrives. */
class SearchViewModel(
    private val services: AppServices,
) : ViewModel() {
    private val mutableUi = MutableStateFlow(SearchUi())
    val ui: StateFlow<SearchUi> = mutableUi.asStateFlow()
    private var job: Job? = null

    fun onQuery(value: String) = mutableUi.update { it.copy(query = value) }

    fun submit() {
        val query = mutableUi.value.query.trim()
        if (query.isEmpty()) return
        job?.cancel()
        mutableUi.update {
            it.copy(
                submitted = query,
                tiers = emptyList(),
                running = true,
                done = false,
                error = null,
                elapsedMs = null,
            )
        }
        job =
            viewModelScope.launch {
                val api = services.session.current() ?: return@launch
                try {
                    api.search(query, EntryFilters.NONE, TIERS).collect { event ->
                        when (event) {
                            is SearchEvent.Tier -> {
                                mutableUi.update {
                                    it.copy(
                                        tiers =
                                            it.tiers + TierResult(event.tier, event.results, event.unavailable),
                                    )
                                }
                            }

                            is SearchEvent.Done -> {
                                val failure =
                                    event.error?.let { streamError ->
                                        val code =
                                            streamError.code?.takeIf {
                                                dev.msj.journal.core.api.ApiError.CODE_PATTERN
                                                    .matches(
                                                        it,
                                                    )
                                            }
                                                ?: "SEARCH_FAILED"
                                        UserError(
                                            code = code,
                                            message = "Search failed on the server: ${streamError.detail}",
                                            hint =
                                                Hints.forCode(code, null)
                                                    ?: "Search again; if it repeats, check the service log.",
                                            details =
                                                listOfNotNull(
                                                    streamError.status?.let {
                                                        dev.msj.journal.core.api.DetailLine(
                                                            "Status",
                                                            it.toString(),
                                                        )
                                                    },
                                                ),
                                        )
                                    }
                                mutableUi.update {
                                    it.copy(
                                        done = true,
                                        running = false,
                                        elapsedMs = event.elapsedMs,
                                        error = failure,
                                    )
                                }
                            }
                        }
                    }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: ApiException) {
                    mutableUi.update { it.copy(running = false, error = UserError.of(error.error)) }
                } finally {
                    mutableUi.update { it.copy(running = false) }
                }
            }
    }

    private companion object {
        val TIERS = listOf(1, 2, 3, 4, 5)
    }
}

private fun tierLabel(tier: Int): String =
    when (tier) {
        1 -> "Exact phrase"
        2 -> "Contains"
        3 -> "Close spelling"
        4 -> "Ranked words"
        5 -> "Similar meaning"
        else -> "Tier $tier"
    }

@Composable
fun SearchScreen(
    viewModel: SearchViewModel,
    onBack: () -> Unit,
    onOpenEntry: (String, NavContext) -> Unit,
    modifier: Modifier = Modifier,
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val focus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    LaunchedEffect(Unit) { if (ui.submitted == null) focus.requestFocus() }
    val c = Journal.colors
    val submit = {
        keyboard?.hide()
        viewModel.submit()
    }

    Column(modifier.fillMaxSize().testTag(TestTags.SEARCH_SCREEN)) {
        TopBar(title = "Search", navigation = { IconAction(JournalIcons.Back, "Back", onBack) })
        Row(
            Modifier.fillMaxWidth().padding(horizontal = Space.gutter, vertical = Space.s3),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Space.s2),
        ) {
            OutlinedTextField(
                value = ui.query,
                onValueChange = viewModel::onQuery,
                modifier = Modifier.weight(1f).focusRequester(focus).testTag(TestTags.SEARCH_FIELD),
                label = { Text("Words or a phrase") },
                singleLine = true,
                textStyle = Journal.type.ui.copy(color = c.ink),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { submit() }),
                shape = JournalShapes.control,
                colors =
                    OutlinedTextFieldDefaults.colors(
                        focusedContainerColor = c.field,
                        unfocusedContainerColor = c.field,
                        focusedBorderColor = c.accent,
                        unfocusedBorderColor = c.fieldBorder,
                        focusedLabelColor = c.accent,
                        unfocusedLabelColor = c.ink2,
                        cursorColor = c.accent,
                    ),
            )
            PrimaryButton("Search", onClick = submit, enabled = ui.query.isNotBlank() && !ui.running)
        }
        if (ui.running) {
            LinearProgressIndicator(
                progress = { ui.tiers.size / 5f },
                modifier = Modifier.fillMaxWidth(),
                color = c.stProgress,
                trackColor = c.rule,
            )
        }
        Status(ui)
        ui.error?.let { ErrorNotice(it, modifier = Modifier.padding(Space.gutter).testTag(TestTags.SEARCH_ERROR)) }
        LazyColumn(Modifier.weight(1f).fillMaxWidth()) {
            ui.tiers.forEach { tier ->
                if (tier.hits.isNotEmpty() || tier.unavailable != null) {
                    item(key = "tier-${tier.tier}") {
                        Row(
                            Modifier.fillMaxWidth().padding(
                                start = Space.gutter,
                                end = Space.gutter,
                                top = Space.s4,
                                bottom = Space.s2,
                            ),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            SectionLabel(tierLabel(tier.tier), Modifier.weight(1f))
                            Text("${tier.hits.size}", style = Journal.type.monoSmall, color = c.ink3)
                        }
                    }
                }
                tier.unavailable?.let { unavailable ->
                    item(key = "unavailable-${tier.tier}") {
                        ErrorNotice(
                            UserError(
                                code = unavailable.code,
                                message = "Similar-meaning search: ${unavailable.detail}",
                                hint = Hints.forCode(unavailable.code, null),
                                details = emptyList(),
                            ),
                            tone = NoticeTone.WARNING,
                            modifier = Modifier.padding(horizontal = Space.gutter),
                        )
                    }
                }
                items(tier.hits, key = { "hit-${it.entryId}" }) { hit ->
                    val entry = hit.entry
                    if (entry != null) {
                        EntryRow(
                            entry = entry,
                            snippet = hit.snippet,
                            tag = TestTags.SEARCH_RESULT_PREFIX + hit.entryId,
                            onOpen = {
                                onOpenEntry(
                                    hit.entryId,
                                    NavContext.chronological(ui.hits.mapNotNull { it.entry }),
                                )
                            },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun Status(ui: SearchUi) {
    val c = Journal.colors
    val submitted = ui.submitted ?: return
    val total = ui.hits.size
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = Space.gutter, vertical = Space.s2)
            .then(if (ui.done) Modifier.testTag(TestTags.SEARCH_DONE) else Modifier)
            .semantics { count = total },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Space.s2),
    ) {
        StatusMarker(
            when {
                ui.error != null -> MarkerKind.FAILED
                ui.done -> MarkerKind.DONE
                else -> MarkerKind.PROGRESS
            },
            label = null,
        )
        Text(
            when {
                ui.done -> {
                    "$total ${if (total == 1) "entry" else "entries"} for \"$submitted\" in ${ui.elapsedMs?.let {
                        "%.0f ms"
                            .format(
                                it,
                            )
                    } ?: "?"}"
                }

                ui.running -> {
                    "Searching: ${ui.tiers.size} of 5 tiers, $total ${if (total == 1) "entry" else "entries"} so far"
                }

                else -> {
                    "$total ${if (total == 1) "entry" else "entries"} for \"$submitted\" (stopped)"
                }
            },
            style = Journal.type.monoSmall,
            color = c.ink2,
        )
    }
}
