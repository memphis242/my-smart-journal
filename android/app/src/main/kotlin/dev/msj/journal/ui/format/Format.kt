package dev.msj.journal.ui.format

import dev.msj.journal.core.api.EntrySummary
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.util.Locale

/** `M:SS`, or `H:MM:SS` from one hour. */
fun formatDuration(ms: Long): String {
    assert(ms >= 0) { "duration is non-negative" }
    val total = ms / 1000
    val hours = total / 3600
    val minutes = (total % 3600) / 60
    val seconds = total % 60
    return if (hours > 0) {
        String.format(Locale.ROOT, "%d:%02d:%02d", hours, minutes, seconds)
    } else {
        String.format(Locale.ROOT, "%d:%02d", minutes, seconds)
    }
}

/** Exact-enough sizes: `812 B`, `41 KB`, `3.4 MB`. */
fun formatSize(bytes: Long): String {
    assert(bytes >= 0) { "size is non-negative" }
    return when {
        bytes < 1024 -> "$bytes B"
        bytes < 1024 * 1024 -> "${bytes / 1024} KB"
        else -> String.format(Locale.ROOT, "%.1f MB", bytes / (1024.0 * 1024.0))
    }
}

private val TIME = DateTimeFormatter.ofPattern("HH:mm", Locale.ROOT)
private val DATE_TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm", Locale.ROOT)

private fun local(timestamp: String): OffsetDateTime? =
    try {
        OffsetDateTime.parse(timestamp).atZoneSameInstant(ZoneId.systemDefault()).toOffsetDateTime()
    } catch (_: DateTimeParseException) {
        null
    }

/** `HH:MM` local (24-hour), or the raw value when it is not a timestamp. */
fun formatTime(timestamp: String): String = local(timestamp)?.format(TIME) ?: timestamp

/** `YYYY-MM-DD HH:MM` local, or the raw value when it is not a timestamp. */
fun formatDateTime(timestamp: String): String = local(timestamp)?.format(DATE_TIME) ?: timestamp

/** `MM-DD` of an ISO date, for the list's date column. */
fun shortDate(isoDate: String): String = if (isoDate.length == 10) isoDate.substring(5) else isoDate

/** The first line of [text], trimmed. */
fun firstLine(text: String): String =
    text
        .trim()
        .lineSequence()
        .firstOrNull()
        ?.trim()
        .orEmpty()

/** The headline a row shows, as on the web: the title, else the summary, else the first line of the text. */
fun headline(entry: EntrySummary): String {
    val title = entry.title?.trim().orEmpty()
    if (title.isNotEmpty()) return title
    val summary = entry.summary?.trim().orEmpty()
    if (summary.isNotEmpty()) return summary
    return firstLine(entry.text)
}

/**
 * One entry as plain text for the clipboard, laid out like the server's `text`
 * export (and the web client's copy), so pasted copies and exports look alike.
 */
fun entryPlainText(entry: EntrySummary): String {
    val lines = mutableListOf<String>()
    val title = entry.title?.trim().orEmpty()
    lines += if (title.isEmpty()) entry.entryDate else "${entry.entryDate}  $title"
    val summary = entry.summary?.trim().orEmpty()
    if (summary.isNotEmpty()) lines += listOf("", "Summary:", summary)
    val text = entry.text.trim()
    if (text.isNotEmpty()) lines += listOf("", text)
    if (entry.tags.isNotEmpty()) lines += listOf("", "Tags:", entry.tags.joinToString(", "))
    return lines.joinToString("\n")
}
