package dev.msj.journal.core.dates

import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException

/**
 * Calendar dates are `YYYY-MM-DD` in the user's local calendar, matching the
 * web client (web/src/lib/dates.ts). [LocalDate] arithmetic has no time of day,
 * so daylight-saving shifts cannot skip or repeat a day.
 */
public enum class RangeKey {
    TODAY,
    WEEK,
    MONTH,
    ALL,
}

/** Inclusive bounds; null on a side means unbounded. */
public data class DateBounds(
    val from: LocalDate?,
    val to: LocalDate?,
) {
    init {
        assert(from == null || to == null || !from.isAfter(to)) { "date bounds are ordered: $from..$to" }
    }

    public val fromIso: String? get() = from?.let(::isoDate)
    public val toIso: String? get() = to?.let(::isoDate)

    public fun contains(date: LocalDate): Boolean =
        (from == null || !date.isBefore(from)) && (to == null || !date.isAfter(to))
}

/**
 * Inclusive bounds for a preset, ending [today]:
 * - TODAY: today only;
 * - WEEK: the last 7 days including today;
 * - MONTH: from the day after the same date one month ago (clamped to the end of a shorter month) to today;
 * - ALL: unbounded.
 */
public fun presetBounds(
    key: RangeKey,
    today: LocalDate,
): DateBounds =
    when (key) {
        RangeKey.TODAY -> DateBounds(today, today)
        RangeKey.WEEK -> DateBounds(today.minusDays(6), today)
        RangeKey.MONTH -> DateBounds(today.minusMonths(1).plusDays(1), today)
        RangeKey.ALL -> DateBounds(null, null)
    }

/** `YYYY-MM-DD`. */
public fun isoDate(date: LocalDate): String = date.format(DateTimeFormatter.ISO_LOCAL_DATE)

/** The date for a strict `YYYY-MM-DD` string that names a real calendar day, else null. */
public fun parseIsoDate(value: String): LocalDate? {
    if (!ISO_DATE.matches(value)) return null
    return try {
        LocalDate.parse(value, DateTimeFormatter.ISO_LOCAL_DATE)
    } catch (_: DateTimeParseException) {
        null
    }
}

private val ISO_DATE = Regex("^\\d{4}-\\d{2}-\\d{2}$")
