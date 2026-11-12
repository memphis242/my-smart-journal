package dev.msj.journal.ui.capture

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.msj.journal.ui.components.ConfirmDialog
import dev.msj.journal.ui.components.ErrorNotice
import dev.msj.journal.ui.components.IconAction
import dev.msj.journal.ui.components.InlineAction
import dev.msj.journal.ui.components.JournalField
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.MarkerKind
import dev.msj.journal.ui.components.PrimaryButton
import dev.msj.journal.ui.components.SecondaryButton
import dev.msj.journal.ui.components.StatusMarker
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.components.TopBar
import dev.msj.journal.ui.format.formatDuration
import dev.msj.journal.ui.format.formatSize
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.Space

@Composable
fun RecorderScreen(
    viewModel: RecorderViewModel,
    onClose: (saved: Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val permission =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission(), viewModel::onPermissionResult)
    var confirmDiscard by rememberSaveable { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        if (ui.phase is RecorderPhase.Ready) {
            val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
            if (granted) viewModel.onPermissionResult(true) else permission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }
    LaunchedEffect(ui.phase) { if (ui.phase is RecorderPhase.Saved) onClose(true) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) viewModel.onBackground() }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    val hasAudio = ui.phase is RecorderPhase.Recording || ui.phase is RecorderPhase.Review
    val close = { if (hasAudio) confirmDiscard = true else onClose(false) }
    BackHandler(onBack = close)
    val c = Journal.colors

    Column(modifier.fillMaxSize().testTag(TestTags.RECORDER_SCREEN)) {
        TopBar(
            title = "Voice entry",
            navigation = { IconAction(JournalIcons.Close, "Close", close) },
        )
        Column(
            Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(horizontal = Space.gutter, vertical = Space.s6),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(Space.s4),
        ) {
            when (val phase = ui.phase) {
                RecorderPhase.Ready, RecorderPhase.Saved -> {
                    Timer(0L)
                    if (ui.permissionDenied) {
                        InlineAction("Ask for the microphone again", onClick = { permission.launch(Manifest.permission.RECORD_AUDIO) })
                    }
                }
                is RecorderPhase.Recording -> {
                    Timer(phase.elapsedMs)
                    LevelMeter(phase.levels)
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.s2)) {
                        StatusMarker(if (phase.paused) MarkerKind.ATTENTION else MarkerKind.PROGRESS, label = null)
                        Text(
                            when {
                                phase.pausedByBackground -> "Paused while the app was in the background"
                                phase.paused -> "Paused"
                                else -> "Recording"
                            },
                            style = Journal.type.ui,
                            color = c.ink2,
                        )
                    }
                }
                is RecorderPhase.Review -> {
                    Timer(phase.durationMs)
                    Text(
                        "Recorded ${formatDuration(phase.durationMs)}, ${formatSize(phase.sizeBytes)}",
                        style = Journal.type.mono,
                        color = c.ink2,
                    )
                    JournalField(value = ui.title, onValueChange = viewModel::onTitle, label = "Title (optional)")
                    JournalField(
                        value = ui.tags,
                        onValueChange = viewModel::onTags,
                        label = "Tags (optional)",
                        supporting = "Separate tags with commas.",
                    )
                    Text(
                        "Saved on this phone first, then uploaded and transcribed on the server.",
                        style = Journal.type.uiSmall,
                        color = c.ink3,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
            ui.error?.let { ErrorNotice(it, modifier = Modifier.testTag(TestTags.RECORDER_ERROR)) }
        }
        Row(
            Modifier
                .fillMaxWidth()
                .windowInsetsPadding(WindowInsets.navigationBars)
                .padding(horizontal = Space.gutter, vertical = Space.s3),
            horizontalArrangement = Arrangement.spacedBy(Space.s3, Alignment.End),
        ) {
            when (val phase = ui.phase) {
                is RecorderPhase.Recording -> {
                    SecondaryButton(
                        text = if (phase.paused) "Resume" else "Pause",
                        onClick = viewModel::togglePause,
                        modifier = Modifier.weight(1f),
                    )
                    PrimaryButton(
                        text = "Stop",
                        onClick = viewModel::stop,
                        icon = JournalIcons.Stop,
                        modifier = Modifier.weight(1f).testTag(TestTags.RECORDER_STOP),
                    )
                }
                is RecorderPhase.Review -> {
                    SecondaryButton(
                        text = "Discard",
                        onClick = { confirmDiscard = true },
                        danger = true,
                        modifier = Modifier.weight(1f).testTag(TestTags.RECORDER_DISCARD),
                    )
                    PrimaryButton(
                        text = "Save",
                        onClick = viewModel::save,
                        enabled = !ui.saving,
                        modifier = Modifier.weight(1f).testTag(TestTags.RECORDER_SAVE),
                    )
                }
                RecorderPhase.Ready ->
                    PrimaryButton(
                        text = "Start recording",
                        onClick = { permission.launch(Manifest.permission.RECORD_AUDIO) },
                        icon = JournalIcons.Mic,
                        modifier = Modifier.weight(1f).testTag(TestTags.RECORDER_START),
                    )
                RecorderPhase.Saved -> Spacer(Modifier.height(Space.control))
            }
        }
    }

    if (confirmDiscard) {
        ConfirmDialog(
            title = "Discard this recording?",
            text = "The recording is not saved anywhere yet. Discarding deletes it from this phone.",
            action = "Discard",
            danger = true,
            onConfirm = {
                confirmDiscard = false
                viewModel.discard()
                onClose(false)
            },
            onDismiss = { confirmDiscard = false },
        )
    }
}

@Composable
private fun Timer(elapsedMs: Long) {
    Text(
        formatDuration(elapsedMs),
        style = Journal.type.monoLarge,
        color = Journal.colors.ink,
        modifier = Modifier.padding(top = Space.s6),
    )
}

/** Recent input levels as thin vertical bars, newest at the right. Neutral ink: a level is not a state. */
@Composable
private fun LevelMeter(levels: List<Float>) {
    val ink = Journal.colors.ink3
    Canvas(
        Modifier
            .fillMaxWidth()
            .height(56.dp)
            .semantics { contentDescription = "Input level" },
    ) {
        val slots = 48
        val step = size.width / slots
        val stroke = (step * 0.45f).coerceAtLeast(2f)
        val middle = size.height / 2
        levels.takeLast(slots).forEachIndexed { index, level ->
            val x = size.width - (levels.size.coerceAtMost(slots) - index - 0.5f) * step
            val half = (0.06f + 0.94f * level) * middle
            drawLine(ink, Offset(x, middle - half), Offset(x, middle + half), stroke, StrokeCap.Round)
        }
    }
}
