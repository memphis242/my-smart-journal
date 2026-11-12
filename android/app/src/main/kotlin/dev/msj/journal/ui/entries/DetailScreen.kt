package dev.msj.journal.ui.entries

import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.PersistableBundle
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.msj.journal.core.api.EntryDetail
import dev.msj.journal.core.api.EntrySource
import dev.msj.journal.core.api.EntryStatus
import dev.msj.journal.core.api.EntrySummary
import dev.msj.journal.ui.components.ErrorNotice
import dev.msj.journal.ui.components.IconAction
import dev.msj.journal.ui.components.InlineAction
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.SecondaryButton
import dev.msj.journal.ui.components.SectionLabel
import dev.msj.journal.ui.components.StatusMarker
import dev.msj.journal.ui.components.TagChip
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.components.TopBar
import dev.msj.journal.ui.components.entryId
import dev.msj.journal.ui.components.labelFor
import dev.msj.journal.ui.components.markerFor
import dev.msj.journal.ui.components.topRule
import dev.msj.journal.ui.format.entryPlainText
import dev.msj.journal.ui.format.formatTime
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.Space
import kotlin.math.abs
import kotlinx.coroutines.launch

@Composable
fun DetailScreen(
    viewModel: DetailViewModel,
    onBack: () -> Unit,
    onEdit: (String) -> Unit,
    onSignInAgain: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val swipeThreshold = with(LocalDensity.current) { 72.dp.toPx() }
    val summary = ui.summary

    Box(modifier.fillMaxSize().testTag(TestTags.DETAIL_SCREEN)) {
        Column(Modifier.fillMaxSize()) {
            TopBar(
                title = summary?.entryDate ?: "Entry",
                subtitle =
                    summary?.let { entry ->
                        "${formatTime(entry.createdAt)}  ${if (entry.source == EntrySource.AUDIO) "Voice" else "Written"}"
                    },
                navigation = { IconAction(JournalIcons.Back, "Back to the list", onBack) },
                actions = {
                    IconAction(
                        JournalIcons.Copy,
                        "Copy entry",
                        onClick = {
                            val entry = summary ?: return@IconAction
                            copyToClipboard(context, entryPlainText(entry))
                            scope.launch { snackbar.showSnackbar("Copied ${entry.entryDate}${entry.title?.let { "  $it" } ?: ""}") }
                        },
                        enabled = summary != null,
                        modifier = Modifier.testTag(TestTags.DETAIL_COPY),
                    )
                    IconAction(
                        JournalIcons.Edit,
                        "Edit entry",
                        onClick = { onEdit(ui.id) },
                        enabled = ui.detail != null,
                        modifier = Modifier.testTag(TestTags.DETAIL_EDIT),
                    )
                },
            )
            AnimatedContent(
                targetState = ui,
                contentKey = { it.id },
                transitionSpec = {
                    val sign = targetState.direction
                    // Newer entries come in from the right, older ones from the left, as the edge controls sit.
                    if (sign == 0) {
                        fadeIn() togetherWith fadeOut()
                    } else {
                        slideInHorizontally { width -> width * sign } togetherWith slideOutHorizontally { width -> -width * sign }
                    }
                },
                modifier =
                    Modifier
                        .weight(1f)
                        .pointerInput(Unit) {
                            var total = 0f
                            detectHorizontalDragGestures(
                                onDragStart = { total = 0f },
                                onDragEnd = {
                                    // Finger moving left brings the newer entry in from the right.
                                    if (abs(total) > swipeThreshold) viewModel.go(if (total < 0) 1 else -1)
                                },
                                onHorizontalDrag = { _, amount -> total += amount },
                            )
                        },
                label = "entry",
            ) { shown ->
                EntryBody(shown, onRetry = viewModel::reload, onSignInAgain = onSignInAgain)
            }
            StepBar(ui, onOlder = { viewModel.go(-1) }, onNewer = { viewModel.go(1) })
        }
        SnackbarHost(
            snackbar,
            Modifier
                .align(Alignment.BottomCenter)
                .windowInsetsPadding(WindowInsets.navigationBars)
                .padding(bottom = 72.dp),
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun EntryBody(
    ui: DetailUi,
    onRetry: () -> Unit,
    onSignInAgain: () -> Unit,
) {
    val c = Journal.colors
    val entry = ui.summary
    var rawOpen by rememberSaveable(ui.id) { mutableStateOf(false) }
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.gutter, vertical = Space.s4)
            .semantics { entryId = ui.id },
        verticalArrangement = Arrangement.spacedBy(Space.s4),
    ) {
        ui.error?.let { error ->
            ErrorNotice(error, modifier = Modifier.testTag(TestTags.DETAIL_ERROR)) {
                if (error.code in SIGN_IN_AGAIN_CODES) InlineAction("Sign in again", onClick = onSignInAgain)
                InlineAction("Try again", onClick = onRetry)
            }
        }
        if (entry == null) {
            Text(if (ui.loading) "Loading the entry" else "The entry could not be shown.", style = Journal.type.ui, color = c.ink3)
            return@Column
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.s2)) {
            StatusMarker(markerFor(entry.status), label = null)
            Text(labelFor(entry.status), style = Journal.type.uiSmall, color = c.ink2)
        }
        entry.title?.takeIf { it.isNotBlank() }?.let {
            SelectionContainer { Text(it, style = Journal.type.proseTitle, color = c.ink) }
        }
        Facets(entry)

        Block("Summary", TestTags.DETAIL_SUMMARY) {
            val text = entry.summary?.trim().orEmpty()
            if (text.isEmpty()) {
                Text(pendingNote(entry.status, "summary"), style = Journal.type.ui, color = c.ink3)
            } else {
                Prose(text)
            }
        }
        Block(if (entry.source == EntrySource.AUDIO) "Cleaned transcript" else "Cleaned text", TestTags.DETAIL_TEXT) {
            val text = entry.text.trim()
            if (text.isEmpty()) Text(pendingNote(entry.status, "text"), style = Journal.type.ui, color = c.ink3) else Prose(text)
        }
        RawBlock(ui.detail, entry, rawOpen) { rawOpen = !rawOpen }
        ui.neighborsError?.let { ErrorNotice(it) }
    }
}

@Composable
private fun Block(
    label: String,
    tag: String,
    content: @Composable () -> Unit,
) {
    Column(Modifier.fillMaxWidth().testTag(tag), verticalArrangement = Arrangement.spacedBy(Space.s2)) {
        SectionLabel(label)
        content()
    }
}

@Composable
private fun RawBlock(
    detail: EntryDetail?,
    entry: EntrySummary,
    open: Boolean,
    onToggle: () -> Unit,
) {
    val c = Journal.colors
    val label = if (entry.source == EntrySource.AUDIO) "Raw transcript" else "Original text"
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(Space.s2)) {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = Space.control)
                .clickable(onClickLabel = if (open) "Hide $label" else "Show $label", onClick = onToggle)
                .testTag(TestTags.DETAIL_RAW_TOGGLE),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SectionLabel(label, Modifier.weight(1f))
            Icon(
                if (open) JournalIcons.ChevronDown else JournalIcons.ChevronRight,
                contentDescription = null,
                tint = c.ink3,
            )
        }
        if (open) {
            val raw = detail?.rawText()?.trim().orEmpty()
            Box(Modifier.testTag(TestTags.DETAIL_RAW)) {
                when {
                    detail == null -> Text("Loading", style = Journal.type.ui, color = c.ink3)
                    raw.isEmpty() -> Text(pendingNote(entry.status, "transcript"), style = Journal.type.ui, color = c.ink3)
                    else -> Prose(raw, muted = true)
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Facets(entry: EntrySummary) {
    val groups = listOf("Tags" to entry.tags, "Projects" to entry.projects, "Skills" to entry.skills).filter { it.second.isNotEmpty() }
    groups.forEach { (label, values) ->
        Column(verticalArrangement = Arrangement.spacedBy(Space.s1)) {
            SectionLabel(label)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(Space.s1), verticalArrangement = Arrangement.spacedBy(Space.s1)) {
                values.forEach { TagChip(it) }
            }
        }
    }
}

@Composable
private fun Prose(
    text: String,
    muted: Boolean = false,
) {
    val c = Journal.colors
    SelectionContainer {
        Column(verticalArrangement = Arrangement.spacedBy(Space.s3)) {
            text.split(Regex("\\n\\s*\\n")).map { it.trim() }.filter { it.isNotEmpty() }.forEach { paragraph ->
                Text(paragraph, style = Journal.type.prose, color = if (muted) c.ink2 else c.ink)
            }
        }
    }
}

private fun pendingNote(
    status: EntryStatus,
    what: String,
): String =
    when (status) {
        EntryStatus.QUEUED, EntryStatus.PROCESSING -> "No $what yet; it appears after the server processes the entry. Run the batch to process it now."
        EntryStatus.FAILED -> "No $what: processing failed. The queue on the list page shows the error."
        EntryStatus.NEEDS_ATTENTION, EntryStatus.DONE -> "No $what for this entry."
    }

/** Older at the left edge, newer at the right edge, as on the web. */
@Composable
private fun StepBar(
    ui: DetailUi,
    onOlder: () -> Unit,
    onNewer: () -> Unit,
) {
    val c = Journal.colors
    Row(
        Modifier
            .fillMaxWidth()
            .topRule(c.rule)
            .windowInsetsPadding(WindowInsets.navigationBars)
            .padding(horizontal = Space.s2, vertical = Space.s2),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SecondaryButton(
            text = if (ui.older == null && ui.neighborsLoading) "Looking" else "Older",
            onClick = onOlder,
            enabled = ui.older != null,
            icon = JournalIcons.ChevronLeft,
            modifier = Modifier.testTag(TestTags.DETAIL_OLDER),
        )
        SecondaryButton(
            text = if (ui.newer == null && ui.neighborsLoading) "Looking" else "Newer",
            onClick = onNewer,
            enabled = ui.newer != null,
            icon = JournalIcons.ChevronRight,
            modifier = Modifier.testTag(TestTags.DETAIL_NEWER),
        )
    }
}

/** Copies as sensitive content, so Android 13+ does not show the text in its clipboard preview. */
fun copyToClipboard(
    context: Context,
    text: String,
) {
    val clipboard = context.getSystemService(ClipboardManager::class.java) ?: return
    val clip = ClipData.newPlainText("Journal entry", text)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        clip.description.extras = PersistableBundle().apply { putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true) }
    }
    clipboard.setPrimaryClip(clip)
}
