package com.riley.assistant.calendar

import android.Manifest
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CalendarContract
import com.riley.assistant.data.Settings
import java.io.IOException
import java.util.TimeZone

data class CalendarInfo(val id: Long, val name: String, val account: String, val primary: Boolean)

data class CalendarEvent(
    val eventId: Long,
    val title: String,
    val begin: Long,
    val end: Long,
    val allDay: Boolean,
    val location: String,
    val calendarId: Long,
    val recurring: Boolean,
)

/**
 * Reads and writes the tablet's calendars (e.g. the Google account's calendar synced to the tablet)
 * through Android's calendar provider. No extra sign-in or API key needed.
 */
object CalendarRepo {

    fun hasPermission(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED &&
            context.checkSelfPermission(Manifest.permission.WRITE_CALENDAR) == PackageManager.PERMISSION_GRANTED

    /** Calendars Riley is allowed to add events to. */
    fun calendars(context: Context): List<CalendarInfo> {
        if (!hasPermission(context)) return emptyList()
        val projection = arrayOf(
            CalendarContract.Calendars._ID,
            CalendarContract.Calendars.CALENDAR_DISPLAY_NAME,
            CalendarContract.Calendars.ACCOUNT_NAME,
            CalendarContract.Calendars.IS_PRIMARY,
        )
        val selection = "${CalendarContract.Calendars.VISIBLE} = 1 AND " +
            "${CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL} >= ${CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR}"
        val result = mutableListOf<CalendarInfo>()
        context.contentResolver.query(CalendarContract.Calendars.CONTENT_URI, projection, selection, null, null)?.use { c ->
            while (c.moveToNext()) {
                result.add(
                    CalendarInfo(
                        id = c.getLong(0),
                        name = c.getString(1).orEmpty(),
                        account = c.getString(2).orEmpty(),
                        primary = !c.isNull(3) && c.getInt(3) == 1,
                    ),
                )
            }
        }
        return result
    }

    fun defaultCalendarId(context: Context): Long? {
        val all = calendars(context)
        val chosen = Settings(context).calendarId
        return all.firstOrNull { it.id == chosen }?.id
            ?: all.firstOrNull { it.primary }?.id
            ?: all.firstOrNull()?.id
    }

    /** Every event occurrence overlapping [from, to), repeating events expanded, declined ones left out. */
    fun events(context: Context, from: Long, to: Long): List<CalendarEvent> {
        if (!hasPermission(context)) return emptyList()
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon().also {
            ContentUris.appendId(it, from)
            ContentUris.appendId(it, to)
        }.build()
        val projection = arrayOf(
            CalendarContract.Instances.EVENT_ID,
            CalendarContract.Instances.TITLE,
            CalendarContract.Instances.BEGIN,
            CalendarContract.Instances.END,
            CalendarContract.Instances.ALL_DAY,
            CalendarContract.Instances.EVENT_LOCATION,
            CalendarContract.Instances.CALENDAR_ID,
            CalendarContract.Instances.RRULE,
            CalendarContract.Instances.SELF_ATTENDEE_STATUS,
        )
        val selection = "${CalendarContract.Instances.VISIBLE} = 1"
        val result = mutableListOf<CalendarEvent>()
        context.contentResolver.query(uri, projection, selection, null, "${CalendarContract.Instances.BEGIN} ASC")?.use { c ->
            while (c.moveToNext()) {
                if (!c.isNull(8) && c.getInt(8) == CalendarContract.Attendees.ATTENDEE_STATUS_DECLINED) continue
                result.add(
                    CalendarEvent(
                        eventId = c.getLong(0),
                        title = c.getString(1)?.takeIf { it.isNotBlank() } ?: "(untitled)",
                        begin = c.getLong(2),
                        end = c.getLong(3),
                        allDay = c.getInt(4) == 1,
                        location = c.getString(5).orEmpty(),
                        calendarId = c.getLong(6),
                        recurring = !c.getString(7).isNullOrBlank(),
                    ),
                )
            }
        }
        return result
    }

    fun event(context: Context, eventId: Long): CalendarEvent? {
        if (!hasPermission(context)) return null
        val projection = arrayOf(
            CalendarContract.Events._ID,
            CalendarContract.Events.TITLE,
            CalendarContract.Events.DTSTART,
            CalendarContract.Events.DTEND,
            CalendarContract.Events.ALL_DAY,
            CalendarContract.Events.EVENT_LOCATION,
            CalendarContract.Events.CALENDAR_ID,
            CalendarContract.Events.RRULE,
            CalendarContract.Events.DELETED,
        )
        val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
        context.contentResolver.query(uri, projection, null, null, null)?.use { c ->
            if (!c.moveToFirst() || c.getInt(8) == 1) return null
            val begin = c.getLong(2)
            return CalendarEvent(
                eventId = c.getLong(0),
                title = c.getString(1).orEmpty(),
                begin = begin,
                end = if (c.isNull(3)) begin else c.getLong(3),
                allDay = c.getInt(4) == 1,
                location = c.getString(5).orEmpty(),
                calendarId = c.getLong(6),
                recurring = !c.getString(7).isNullOrBlank(),
            )
        }
        return null
    }

    fun addEvent(context: Context, title: String, begin: Long, end: Long, location: String, notes: String): Long {
        val calendarId = defaultCalendarId(context) ?: throw IOException("no writable calendar on this tablet")
        val values = ContentValues().apply {
            put(CalendarContract.Events.CALENDAR_ID, calendarId)
            put(CalendarContract.Events.TITLE, title)
            put(CalendarContract.Events.DTSTART, begin)
            put(CalendarContract.Events.DTEND, end)
            put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().id)
            if (location.isNotBlank()) put(CalendarContract.Events.EVENT_LOCATION, location)
            if (notes.isNotBlank()) put(CalendarContract.Events.DESCRIPTION, notes)
        }
        val uri = context.contentResolver.insert(CalendarContract.Events.CONTENT_URI, values)
            ?: throw IOException("the calendar refused the event")
        return ContentUris.parseId(uri)
    }

    fun updateEvent(context: Context, eventId: Long, values: ContentValues): Boolean {
        val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
        return context.contentResolver.update(uri, values, null, null) > 0
    }

    fun deleteEvent(context: Context, eventId: Long): Boolean {
        val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
        return context.contentResolver.delete(uri, null, null) > 0
    }
}
