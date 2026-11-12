package dev.msj.journal.core.dates

import org.junit.Test
import java.time.LocalDate
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class DateRangesTest {
    private fun bounds(
        key: RangeKey,
        today: String,
    ) = presetBounds(key, LocalDate.parse(today))

    @Test
    fun `today and week`() {
        assertEquals(
            DateBounds(LocalDate.parse("2026-11-11"), LocalDate.parse("2026-11-11")),
            bounds(RangeKey.TODAY, "2026-11-11"),
        )
        val week = bounds(RangeKey.WEEK, "2026-11-11")
        assertEquals("2026-11-05", week.fromIso)
        assertEquals("2026-11-11", week.toIso)
        assertEquals("2026-12-27", bounds(RangeKey.WEEK, "2027-01-02").fromIso)
    }

    @Test
    fun `month starts the day after the same date a month earlier`() {
        assertEquals("2026-10-12", bounds(RangeKey.MONTH, "2026-11-11").fromIso)
        assertEquals("2026-03-01", bounds(RangeKey.MONTH, "2026-03-31").fromIso)
        assertEquals("2028-03-01", bounds(RangeKey.MONTH, "2028-03-30").fromIso)
        assertEquals("2026-12-31", bounds(RangeKey.MONTH, "2027-01-30").fromIso)
    }

    @Test
    fun `all is unbounded and contains everything`() {
        val all = bounds(RangeKey.ALL, "2026-11-11")
        assertNull(all.fromIso)
        assertNull(all.toIso)
        assertTrue(all.contains(LocalDate.parse("1999-01-01")))
    }

    @Test
    fun `contains is inclusive on both ends`() {
        val week = bounds(RangeKey.WEEK, "2026-11-11")
        assertTrue(week.contains(LocalDate.parse("2026-11-05")))
        assertTrue(week.contains(LocalDate.parse("2026-11-11")))
        assertFalse(week.contains(LocalDate.parse("2026-11-04")))
        assertFalse(week.contains(LocalDate.parse("2026-11-12")))
    }

    @Test
    fun `parseIsoDate accepts real dates only`() {
        assertEquals(LocalDate.parse("2028-02-29"), parseIsoDate("2028-02-29"))
        assertNull(parseIsoDate("2026-02-30"))
        assertNull(parseIsoDate("2026-02-29"))
        assertNull(parseIsoDate("2026-2-3"))
        assertNull(parseIsoDate("2026-11-1"))
        assertNull(parseIsoDate(" 2026-11-11"))
        assertNull(parseIsoDate("2026-11-11T00:00"))
        assertNull(parseIsoDate(""))
    }

    @Test
    fun `bounds must be ordered`() {
        assertFailsWith<AssertionError> { DateBounds(LocalDate.parse("2026-11-12"), LocalDate.parse("2026-11-11")) }
    }
}
