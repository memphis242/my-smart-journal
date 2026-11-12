package dev.msj.journal.ui.entries

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import dev.msj.journal.core.api.Hints
import dev.msj.journal.core.dates.RangeKey
import dev.msj.journal.core.queue.QueueLogic
import dev.msj.journal.ui.UserError
import dev.msj.journal.ui.components.Band
import dev.msj.journal.ui.components.ErrorNotice
import dev.msj.journal.ui.components.IconAction
import dev.msj.journal.ui.components.InlineAction
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.MarkerKind
import dev.msj.journal.ui.components.NoticeTone
import dev.msj.journal.ui.components.PrimaryButton
import dev.msj.journal.ui.components.SecondaryButton
import dev.msj.journal.ui.components.SegmentedChoice
import dev.msj.journal.ui.components.StatusMarker
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.components.TopBar
import dev.msj.journal.ui.components.count
import dev.msj.journal.ui.components.topRule
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.Space

/** Errors that mean the saved token no longer works; the notice offers to sign in again. */
val SIGN_IN_AGAIN_CODES: Set<String> = setOf("UNAUTHORIZED", "TOKEN_REISSUE_REQUIRED", "BAD_TOKEN")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    viewModel: HomeViewModel,
    host: String,
    localNetworkBlocked: Boolean,
    onAllowLocalNetwork: () -> Unit,
    onOpenEntry: (String, NavContext) -> Unit,
    onSearch: () -> Unit,
    onWrite: () -> Unit,
    onRecord: () -> Unit,
    onOutbox: () -> Unit,
    onSettings: () -> Unit,
    onSignInAgain: () -> Unit,
    themeSwitch: @Composable () -> Unit,
    snackbar: SnackbarHostState,
    modifier: Modifier = Modifier,
) {
    val list by viewModel.list.collectAsStateWithLifecycle()
    val queue by viewModel.queue.collectAsStateWithLifecycle()
    val unsent by viewModel.unsent.collectAsStateWithLifecycle()
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            viewModel.onVisible()
            viewModel.pollQueue()
        }
    }
    LaunchedEffect(viewModel) { viewModel.messages.collect { snackbar.showSnackbar(it) } }
    var menuOpen by rememberSaveable { mutableStateOf(false) }
    var filterOpen by rememberSaveable { mutableStateOf(false) }
    val facets by viewModel.facets.collectAsStateWithLifecycle()

    Box(modifier.fillMaxSize().testTag(TestTags.HOME_SCREEN)) {
        Column(Modifier.fillMaxSize()) {
            TopBar(
                title = "Journal",
                subtitle = host,
                actions = {
                    IconAction(
                        JournalIcons.Search,
                        "Search entries",
                        onSearch,
                        modifier = Modifier.testTag(TestTags.SEARCH_OPEN),
                    )
                    FilterAction(
                        selected = list.selection.size,
                        onOpen = {
                            filterOpen = true
                            viewModel.loadFacets()
                        },
                    )
                    themeSwitch()
                    Box {
                        IconAction(
                            JournalIcons.More,
                            "More",
                            { menuOpen = true },
                            modifier = Modifier.testTag(TestTags.MENU),
                        )
                        DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                            DropdownMenuItem(
                                text = { Text("Unsent uploads", style = Journal.type.ui) },
                                leadingIcon = { Icon(JournalIcons.Outbox, contentDescription = null) },
                                onClick = {
                                    menuOpen = false
                                    onOutbox()
                                },
                                modifier = Modifier.testTag(TestTags.MENU_OUTBOX),
                            )
                            DropdownMenuItem(
                                text = { Text("Settings and sign-out", style = Journal.type.ui) },
                                leadingIcon = { Icon(JournalIcons.Settings, contentDescription = null) },
                                onClick = {
                                    menuOpen = false
                                    onSettings()
                                },
                                modifier = Modifier.testTag(TestTags.MENU_SETTINGS),
                            )
                        }
                    }
                },
            )
            SegmentedChoice(
                options = RangeKey.entries,
                selected = list.range,
                label = ::rangeLabel,
                onSelect = viewModel::selectRange,
                tag = { TestTags.RANGE_PREFIX + it.name },
                modifier = Modifier.padding(horizontal = Space.gutter, vertical = Space.s3),
            )
            if (list.selection.size > 0) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = Space.gutter)
                        .testTag(TestTags.FILTER_ACTIVE),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        (list.selection.tags + list.selection.projects + list.selection.skills).sorted().joinToString(
                            ", ",
                        ),
                        style = Journal.type.monoSmall,
                        color = Journal.colors.accent,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    InlineAction(
                        "Clear filters",
                        onClick = viewModel::clearFacets,
                        modifier = Modifier.testTag(TestTags.FILTER_CLEAR),
                    )
                }
            }
            QueueBand(queue, stale = viewModel.isWorkerStale(queue), onRunNow = viewModel::runNow)
            QueueNotices(queue, onDismiss = viewModel::dismissRunError, onSignInAgain = onSignInAgain)
            if (unsent.total > 0) UnsentBand(unsent, onOutbox)
            if (localNetworkBlocked) {
                ErrorNotice(
                    error =
                        UserError(
                            code = "LOCAL_NETWORK_PERMISSION",
                            message =
                                "Local network access is off, so Android blocks the connection to the journal server.",
                            hint = "The server has a private-network address. Allow local network access to reach it.",
                            details = emptyList(),
                        ),
                    tone = NoticeTone.WARNING,
                    modifier =
                        Modifier
                            .padding(
                                horizontal = Space.gutter,
                                vertical = Space.s2,
                            ).testTag(TestTags.LOCAL_NETWORK_NOTICE),
                ) { InlineAction("Allow", onClick = onAllowLocalNetwork) }
            }
            PullToRefreshBox(
                isRefreshing = list.loading && list.loaded,
                onRefresh = viewModel::refresh,
                modifier = Modifier.weight(1f).fillMaxWidth(),
            ) {
                EntryList(list, viewModel::loadMore, onOpenEntry, onSignInAgain, onRetry = viewModel::refresh)
            }
            CaptureBar(onWrite = onWrite, onRecord = onRecord)
        }
        if (filterOpen) {
            FilterSheet(
                facets = facets,
                selection = list.selection,
                onToggle = viewModel::toggleFacet,
                onClear = viewModel::clearFacets,
                onDismiss = { filterOpen = false },
            )
        }
        SnackbarHost(
            snackbar,
            modifier =
                Modifier
                    .align(Alignment.BottomCenter)
                    .windowInsetsPadding(WindowInsets.navigationBars)
                    .padding(bottom = 76.dp)
                    .testTag(TestTags.SNACKBAR),
        )
    }
}

fun rangeLabel(range: RangeKey): String =
    when (range) {
        RangeKey.TODAY -> "Today"
        RangeKey.WEEK -> "Week"
        RangeKey.MONTH -> "Month"
        RangeKey.ALL -> "All"
    }

@Composable
private fun EntryList(
    list: ListState,
    onLoadMore: () -> Unit,
    onOpenEntry: (String, NavContext) -> Unit,
    onSignInAgain: () -> Unit,
    onRetry: () -> Unit,
) {
    val c = Journal.colors
    val state = rememberLazyListState()
    val latestLoadMore by rememberUpdatedState(onLoadMore)
    LaunchedEffect(state, list.entries.size) {
        snapshotFlow {
            state.layoutInfo.visibleItemsInfo
                .lastOrNull()
                ?.index ?: 0
        }.collect { last ->
            if (last >= list.entries.size - LOAD_AHEAD) latestLoadMore()
        }
    }
    LazyColumn(Modifier.fillMaxSize().testTag(TestTags.ENTRY_LIST), state = state) {
        list.error?.let { error ->
            item(key = "error") {
                ErrorNotice(error, modifier = Modifier.padding(Space.gutter).testTag(TestTags.ENTRIES_ERROR)) {
                    if (error.code in SIGN_IN_AGAIN_CODES) {
                        InlineAction(
                            "Sign in again",
                            onClick = onSignInAgain,
                            modifier = Modifier.testTag(TestTags.SIGN_IN_AGAIN),
                        )
                    } else {
                        InlineAction("Try again", onClick = onRetry)
                    }
                }
            }
        }
        if (list.loaded && list.entries.isEmpty() && list.error == null) {
            item(key = "empty") {
                Text(
                    "No entries in this range yet. What you write or record appears here once the server has it.",
                    style = Journal.type.ui,
                    color = c.ink3,
                    modifier = Modifier.padding(Space.gutter).testTag(TestTags.ENTRIES_EMPTY),
                )
            }
        }
        items(list.entries, key = { it.id }) { entry ->
            EntryRow(
                entry = entry,
                onOpen = { onOpenEntry(entry.id, NavContext.fromList(list.entries, list.filters, list.complete)) },
            )
        }
        if (list.entries.isNotEmpty()) {
            item(key = "footer") {
                Text(
                    if (list.complete) {
                        "${list.total} ${if (list.total == 1) "entry" else "entries"}"
                    } else {
                        "${list.entries.size} of ${list.total} loaded"
                    },
                    style = Journal.type.monoSmall,
                    color = c.ink3,
                    modifier = Modifier.fillMaxWidth().padding(Space.gutter),
                )
            }
        }
    }
}

@Composable
private fun QueueBand(
    queue: QueueState,
    stale: Boolean,
    onRunNow: () -> Unit,
) {
    val c = Journal.colors
    val snapshot = queue.snapshot
    Band(Modifier.testTag(TestTags.QUEUE_STRIP)) {
        Row(
            Modifier.weight(1f),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Space.s3),
        ) {
            when {
                snapshot == null && queue.error != null -> {
                    StatusMarker(MarkerKind.FAILED, label = null)
                    Text("Queue unavailable", style = Journal.type.uiSmall, color = c.ink2)
                    Text(queue.error.code, style = Journal.type.monoSmall, color = c.stFailed)
                }

                snapshot == null -> {
                    Text("Queue", style = Journal.type.uiSmall, color = c.ink3)
                }

                else -> {
                    QueueCount("queued", snapshot.counts.queued, MarkerKind.QUEUED)
                    QueueCount("running", snapshot.counts.running, MarkerKind.PROGRESS)
                    QueueCount("failed", snapshot.counts.failed, MarkerKind.FAILED)
                    if (stale) {
                        Row(
                            Modifier.testTag(TestTags.QUEUE_STALE),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(Space.s1),
                        ) {
                            StatusMarker(MarkerKind.ATTENTION, label = null)
                            Text("Worker stopped", style = Journal.type.uiSmall, color = c.stAttention)
                        }
                    }
                }
            }
        }
        SecondaryButton(
            text = if (queue.runPending) "Requesting" else "Run now",
            onClick = onRunNow,
            enabled = !queue.runPending && snapshot != null,
            modifier = Modifier.padding(vertical = Space.s1).testTag(TestTags.QUEUE_RUN_NOW),
        )
    }
}

@Composable
private fun QueueCount(
    name: String,
    value: Int,
    marker: MarkerKind,
) {
    val c = Journal.colors
    Row(
        Modifier
            .testTag(TestTags.QUEUE_COUNT_PREFIX + name)
            .semantics(mergeDescendants = true) { count = value },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Space.s1),
    ) {
        StatusMarker(if (value == 0) MarkerKind.MUTED else marker, label = null, size = 10.dp)
        Text("$value", style = Journal.type.mono, color = c.ink)
        Text(name, style = Journal.type.uiSmall, color = c.ink3)
    }
}

@Composable
private fun QueueNotices(
    queue: QueueState,
    onDismiss: () -> Unit,
    onSignInAgain: () -> Unit,
) {
    Column(Modifier.fillMaxWidth()) {
        val padded = Modifier.padding(horizontal = Space.gutter, vertical = Space.s2)
        val runError = queue.runError
        when {
            runError != null -> {
                ErrorNotice(runError, modifier = padded.testTag(TestTags.QUEUE_ERROR)) {
                    if (runError.code in SIGN_IN_AGAIN_CODES) InlineAction("Sign in again", onClick = onSignInAgain)
                    InlineAction("Dismiss", onClick = onDismiss)
                }
            }

            queue.requestedWithStaleWorker -> {
                ErrorNotice(
                    UserError(
                        code = "WORKER_NOT_RUNNING",
                        message = "The batch is requested, but no worker is running to pick it up.",
                        hint = Hints.forCode("WORKER_NOT_RUNNING", null),
                        details = emptyList(),
                    ),
                    tone = NoticeTone.WARNING,
                    modifier = padded.testTag(TestTags.QUEUE_ERROR),
                ) { InlineAction("Dismiss", onClick = onDismiss) }
            }
        }
        val lastBatch = queue.snapshot?.let(QueueLogic::lastBatchError)
        if (lastBatch != null && runError == null) {
            ErrorNotice(
                UserError.of(lastBatch),
                tone = NoticeTone.WARNING,
                modifier = padded.testTag(TestTags.QUEUE_LAST_BATCH_ERROR),
            )
        }
    }
}

@Composable
private fun UnsentBand(
    unsent: UnsentSummary,
    onOpen: () -> Unit,
) {
    val c = Journal.colors
    Band(
        Modifier
            .clickable(onClickLabel = "Open unsent uploads", onClick = onOpen)
            .testTag(TestTags.UNSENT_STRIP)
            .semantics { count = unsent.total },
    ) {
        StatusMarker(if (unsent.rejected > 0) MarkerKind.FAILED else MarkerKind.BLOCKED, label = null)
        Text(
            buildString {
                append(unsent.total)
                    .append(
                        if (unsent.total ==
                            1
                        ) {
                            " entry"
                        } else {
                            " entries"
                        },
                    ).append(" waiting to upload")
                if (unsent.rejected > 0) append(", ").append(unsent.rejected).append(" rejected")
            },
            style = Journal.type.uiSmall,
            color = if (unsent.rejected > 0) c.stFailed else c.ink2,
            modifier = Modifier.weight(1f).padding(start = Space.s2),
        )
        Icon(JournalIcons.ChevronRight, contentDescription = null, tint = c.ink3)
    }
}

/** Capture is the phone's main job: Record (primary) and Write, pinned at the bottom within thumb reach. */
@Composable
private fun CaptureBar(
    onWrite: () -> Unit,
    onRecord: () -> Unit,
) {
    val c = Journal.colors
    Row(
        Modifier
            .fillMaxWidth()
            .topRule(c.rule)
            .windowInsetsPadding(WindowInsets.navigationBars)
            .padding(horizontal = Space.gutter, vertical = Space.s3),
        horizontalArrangement = Arrangement.spacedBy(Space.s3),
    ) {
        SecondaryButton(
            "Write",
            onWrite,
            icon = JournalIcons.Edit,
            modifier = Modifier.weight(1f).testTag(TestTags.CAPTURE_WRITE),
        )
        PrimaryButton(
            "Record",
            onRecord,
            icon = JournalIcons.Mic,
            modifier = Modifier.weight(1f).testTag(TestTags.CAPTURE_RECORD),
        )
    }
}

private const val LOAD_AHEAD = 10
