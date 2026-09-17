package com.riley.assistant.data

import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

object TimeUtil {
    private val input = DateTimeFormatter.ofPattern("yyyy-MM-dd H:mm")
    private val display = DateTimeFormatter.ofPattern("EEE d MMM, HH:mm", Locale.ENGLISH)

    /** Parses "yyyy-MM-dd HH:mm" or "yyyy-MM-dd" (09:00) in the device's time zone. */
    fun parse(text: String): Long? {
        val cleaned = text.trim().replace('T', ' ')
        val local = runCatching { LocalDateTime.parse(cleaned, input) }.getOrNull()
            ?: runCatching { LocalDate.parse(cleaned).atTime(9, 0) }.getOrNull()
            ?: return null
        return local.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
    }

    private val clock = DateTimeFormatter.ofPattern("HH:mm", Locale.ENGLISH)
    private val day = DateTimeFormatter.ofPattern("EEE d MMM", Locale.ENGLISH)

    fun format(millis: Long): String = display.format(Instant.ofEpochMilli(millis).atZone(ZoneId.systemDefault()))

    fun formatClock(millis: Long): String = clock.format(Instant.ofEpochMilli(millis).atZone(ZoneId.systemDefault()))

    fun formatRange(begin: Long, end: Long, allDay: Boolean): String {
        if (allDay) {
            // All-day events are stored in UTC midnight.
            return day.format(Instant.ofEpochMilli(begin).atZone(ZoneId.of("UTC"))) + " (all day)"
        }
        val zone = ZoneId.systemDefault()
        val sameDay = Instant.ofEpochMilli(begin).atZone(zone).toLocalDate() == Instant.ofEpochMilli(end).atZone(zone).toLocalDate()
        return if (sameDay) "${format(begin)}–${formatClock(end)}" else "${format(begin)} to ${format(end)}"
    }

    fun startOfToday(): Long = LocalDate.now().atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli()

    fun startOfDayAfter(days: Long): Long =
        LocalDate.now().plusDays(days).atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli()

    /** Moves a repeating due time forward until it is in the future. */
    fun advancePast(millis: Long, repeat: String, now: Long): Long {
        if (repeat == "none") return millis
        var time = Instant.ofEpochMilli(millis).atZone(ZoneId.systemDefault())
        while (time.toInstant().toEpochMilli() <= now) {
            time = when (repeat) {
                "daily" -> time.plusDays(1)
                "weekly" -> time.plusWeeks(1)
                "monthly" -> time.plusMonths(1)
                else -> return millis
            }
        }
        return time.toInstant().toEpochMilli()
    }
}
