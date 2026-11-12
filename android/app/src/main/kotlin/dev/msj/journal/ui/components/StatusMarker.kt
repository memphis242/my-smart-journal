package dev.msj.journal.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.msj.journal.core.api.EntryStatus
import dev.msj.journal.core.outbox.OutboxState
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.JournalColors

/**
 * Hollow state markers. Shape and color both encode the state, so a marker
 * still reads without color: ring = not started, half-filled ring = in
 * progress, filled disc = complete, triangle = needs attention, cross =
 * failed, dashed ring = waiting on something else.
 */
enum class MarkerKind {
    QUEUED,
    PROGRESS,
    DONE,
    ATTENTION,
    FAILED,
    BLOCKED,
    MUTED,
}

fun markerFor(status: EntryStatus): MarkerKind =
    when (status) {
        EntryStatus.QUEUED -> MarkerKind.QUEUED
        EntryStatus.PROCESSING -> MarkerKind.PROGRESS
        EntryStatus.DONE -> MarkerKind.DONE
        EntryStatus.NEEDS_ATTENTION -> MarkerKind.ATTENTION
        EntryStatus.FAILED -> MarkerKind.FAILED
    }

fun labelFor(status: EntryStatus): String =
    when (status) {
        EntryStatus.QUEUED -> "Queued for processing"
        EntryStatus.PROCESSING -> "Processing"
        EntryStatus.DONE -> "Processed"
        EntryStatus.NEEDS_ATTENTION -> "Needs attention"
        EntryStatus.FAILED -> "Processing failed"
    }

fun markerFor(state: OutboxState): MarkerKind =
    when (state) {
        OutboxState.PENDING -> MarkerKind.QUEUED
        OutboxState.UPLOADING -> MarkerKind.PROGRESS
        OutboxState.RETRY_WAIT -> MarkerKind.BLOCKED
        OutboxState.UNCERTAIN -> MarkerKind.BLOCKED
        OutboxState.FAILED -> MarkerKind.FAILED
        OutboxState.SENT -> MarkerKind.DONE
    }

fun labelFor(state: OutboxState): String =
    when (state) {
        OutboxState.PENDING -> "Waiting to upload"
        OutboxState.UPLOADING -> "Uploading"
        OutboxState.RETRY_WAIT -> "Upload failed; retrying"
        OutboxState.UNCERTAIN -> "Checking whether the server received it"
        OutboxState.FAILED -> "Upload rejected; needs you"
        OutboxState.SENT -> "Uploaded"
    }

fun JournalColors.forMarker(kind: MarkerKind): Color =
    when (kind) {
        MarkerKind.QUEUED, MarkerKind.BLOCKED -> stQueued
        MarkerKind.PROGRESS -> stProgress
        MarkerKind.DONE -> stDone
        MarkerKind.ATTENTION -> stAttention
        MarkerKind.FAILED -> stFailed
        MarkerKind.MUTED -> ink3
    }

/**
 * Draws the marker in a 12-unit box scaled to [size]. [label] is the accessible
 * name; pass null when visible text next to the marker already says the state.
 */
@Composable
fun StatusMarker(
    kind: MarkerKind,
    label: String?,
    modifier: Modifier = Modifier,
    size: Dp = 12.dp,
) {
    val color = Journal.colors.forMarker(kind)
    val described =
        if (label == null) modifier else modifier.semantics { contentDescription = label }
    Canvas(described.size(size)) {
        val unit = this.size.minDimension / 12f
        val stroke = Stroke(width = 1.5f * unit, cap = StrokeCap.Round, join = StrokeJoin.Round)
        val center = Offset(6f * unit, 6f * unit)
        val radius = 4.25f * unit
        when (kind) {
            MarkerKind.QUEUED, MarkerKind.MUTED -> drawCircle(color, radius, center, style = stroke)
            MarkerKind.BLOCKED ->
                drawCircle(
                    color,
                    radius,
                    center,
                    style =
                        Stroke(
                            width = 1.5f * unit,
                            pathEffect = PathEffect.dashPathEffect(floatArrayOf(2.2f * unit, 1.9f * unit)),
                        ),
                )
            MarkerKind.PROGRESS -> {
                drawCircle(color, radius, center, style = stroke)
                drawArc(
                    color = color,
                    startAngle = 0f,
                    sweepAngle = 180f,
                    useCenter = true,
                    topLeft = Offset(center.x - radius, center.y - radius),
                    size = Size(radius * 2, radius * 2),
                )
            }
            MarkerKind.DONE -> {
                drawCircle(color, radius, center)
                drawCircle(color, radius, center, style = stroke)
            }
            MarkerKind.ATTENTION -> {
                val path =
                    Path().apply {
                        moveTo(6f * unit, 1.4f * unit)
                        lineTo(10.7f * unit, 10f * unit)
                        lineTo(1.3f * unit, 10f * unit)
                        close()
                    }
                drawPath(path, color, style = stroke)
            }
            MarkerKind.FAILED -> {
                drawLine(color, Offset(2.4f * unit, 2.4f * unit), Offset(9.6f * unit, 9.6f * unit), 1.8f * unit, StrokeCap.Round)
                drawLine(color, Offset(9.6f * unit, 2.4f * unit), Offset(2.4f * unit, 9.6f * unit), 1.8f * unit, StrokeCap.Round)
            }
        }
    }
}
