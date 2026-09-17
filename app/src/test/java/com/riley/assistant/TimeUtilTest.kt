package com.riley.assistant

import com.riley.assistant.data.TimeUtil
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId

class TimeUtilTest {

    @Test
    fun `parses the formats the model is told to use`() {
        assertNotNull(TimeUtil.parse("2026-09-18 09:00"))
        assertNotNull(TimeUtil.parse("2026-09-18 9:00"))
        assertNotNull(TimeUtil.parse("2026-09-18T09:00"))
        // A date on its own means 09:00 that day.
        val dateOnly = TimeUtil.parse("2026-09-18")
        assertNotNull(dateOnly)
        assertEquals(9, LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(dateOnly!!), ZoneId.systemDefault()).hour)
    }

    @Test
    fun `rejects what it cannot understand`() {
        listOf("tomorrow evening", "18-09-2026", "", "next week").forEach { assertNull(it, TimeUtil.parse(it)) }
    }

    @Test
    fun `repeating tasks move past now, keeping the time of day`() {
        val zone = ZoneId.systemDefault()
        val past = LocalDateTime.now(zone).minusDays(3).withHour(7).withMinute(30).withSecond(0).withNano(0)
        val start = past.atZone(zone).toInstant().toEpochMilli()
        val now = System.currentTimeMillis()

        val daily = TimeUtil.advancePast(start, "daily", now)
        assertTrue(daily > now)
        val moved = LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(daily), zone)
        assertEquals(7, moved.hour)
        assertEquals(30, moved.minute)

        val weekly = TimeUtil.advancePast(start, "weekly", now)
        assertTrue(weekly > now)
        assertEquals(past.dayOfWeek, LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(weekly), zone).dayOfWeek)

        // A one-off task is never moved.
        assertEquals(start, TimeUtil.advancePast(start, "none", now))
    }
}
