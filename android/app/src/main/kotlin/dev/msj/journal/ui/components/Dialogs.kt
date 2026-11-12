package dev.msj.journal.ui.components

import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDefaults
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Text
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import dev.msj.journal.core.dates.isoDate
import dev.msj.journal.core.dates.parseIsoDate
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.JournalShapes
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset

/**
 * A confirmation that says exactly what will happen, with Cancel and the
 * named action. [danger] colors the action as destructive.
 */
@Composable
fun ConfirmDialog(
    title: String,
    text: String,
    action: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    danger: Boolean = false,
) {
    val c = Journal.colors
    AlertDialog(
        onDismissRequest = onDismiss,
        shape = JournalShapes.sheet,
        containerColor = c.surfaceRaised,
        title = { Text(title, style = Journal.type.barTitle, color = c.ink) },
        text = { Text(text, style = Journal.type.ui, color = c.ink2) },
        confirmButton = {
            if (danger) {
                SecondaryButton(action, onConfirm, danger = true, modifier = Modifier.testTag(TestTags.CONFIRM_ACTION))
            } else {
                PrimaryButton(action, onConfirm, modifier = Modifier.testTag(TestTags.CONFIRM_ACTION))
            }
        },
        dismissButton = {
            SecondaryButton("Cancel", onDismiss, modifier = Modifier.testTag(TestTags.CONFIRM_CANCEL))
        },
    )
}

/** Picks a journal date (`YYYY-MM-DD`); future days are not offered. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun JournalDatePicker(
    date: String,
    today: LocalDate,
    onPick: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    val initial = parseIsoDate(date) ?: today
    val todayMillis = today.atStartOfDay().toInstant(ZoneOffset.UTC).toEpochMilli()
    val state =
        rememberDatePickerState(
            initialSelectedDateMillis = initial.atStartOfDay().toInstant(ZoneOffset.UTC).toEpochMilli(),
            selectableDates =
                object : androidx.compose.material3.SelectableDates {
                    override fun isSelectableDate(utcTimeMillis: Long): Boolean = utcTimeMillis <= todayMillis
                },
        )
    val c = Journal.colors
    DatePickerDialog(
        onDismissRequest = onDismiss,
        confirmButton = {
            PrimaryButton(
                "Use date",
                onClick = {
                    val millis = state.selectedDateMillis
                    if (millis != null) onPick(isoDate(Instant.ofEpochMilli(millis).atOffset(ZoneOffset.UTC).toLocalDate()))
                    onDismiss()
                },
            )
        },
        dismissButton = { SecondaryButton("Cancel", onDismiss) },
        colors = DatePickerDefaults.colors(containerColor = c.surfaceRaised),
    ) {
        DatePicker(state = state, showModeToggle = true)
    }
}
