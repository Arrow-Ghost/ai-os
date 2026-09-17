package com.riley.assistant.alerts

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.riley.assistant.MainActivity
import com.riley.assistant.R
import com.riley.assistant.RileyApp
import com.riley.assistant.calendar.CalendarRepo
import com.riley.assistant.data.Settings
import com.riley.assistant.killswitch.KillSwitch
import com.riley.assistant.voice.RileyVoice
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId

/**
 * Riley's own clock: meeting heads-ups, a calendar re-check every 30 minutes, and the daily morning brief.
 * Each uses one fixed alarm slot, so the kill switch can cancel all of them reliably.
 */
object Alerts {
    private const val SLOT_MEETING = 9001
    private const val SLOT_SYNC = 9002
    private const val SLOT_BRIEF = 9003

    const val ACTION_MEETING = "com.riley.assistant.MEETING_HEADS_UP"
    const val ACTION_SYNC = "com.riley.assistant.CALENDAR_SYNC"
    const val ACTION_BRIEF = "com.riley.assistant.MORNING_BRIEF"
    const val EXTRA_EVENT_ID = "event_id"
    const val EXTRA_BEGIN = "begin"

    private const val SYNC_EVERY_MS = 30 * 60_000L
    private const val BRIEF_WORK = "morning-brief"
    const val NOTIFY_MEETING = 8001
    const val NOTIFY_BRIEF = 8002

    fun rescheduleAll(context: Context) {
        if (KillSwitch.detonated) return
        scheduleNextMeeting(context)
        scheduleSync(context)
        scheduleBrief(context)
    }

    fun cancelAll(context: Context) {
        listOf(SLOT_MEETING to ACTION_MEETING, SLOT_SYNC to ACTION_SYNC, SLOT_BRIEF to ACTION_BRIEF).forEach { (slot, action) ->
            pending(context, slot, action, PendingIntent.FLAG_NO_CREATE)?.let {
                context.getSystemService(AlarmManager::class.java).cancel(it)
                it.cancel()
            }
        }
        runCatching { WorkManager.getInstance(context).cancelAllWork() }
    }

    /** Arms one alarm for the next meeting that hasn't been announced yet. */
    fun scheduleNextMeeting(context: Context) {
        cancelSlot(context, SLOT_MEETING, ACTION_MEETING)
        val settings = Settings(context)
        if (KillSwitch.detonated || !settings.meetingAlerts || !CalendarRepo.hasPermission(context)) return
        val now = System.currentTimeMillis()
        val lead = settings.meetingLeadMinutes * 60_000L
        val announced = settings.announcedMeetings
        val next = runCatching { CalendarRepo.events(context, now, now + 2 * 24 * 3_600_000L) }.getOrDefault(emptyList())
            .filter { !it.allDay && it.begin > now && meetingKey(it.eventId, it.begin) !in announced }
            .minByOrNull { it.begin } ?: return
        val intent = Intent(context, AlertReceiver::class.java)
            .setAction(ACTION_MEETING)
            .putExtra(EXTRA_EVENT_ID, next.eventId)
            .putExtra(EXTRA_BEGIN, next.begin)
        setAlarm(context, SLOT_MEETING, intent, maxOf(next.begin - lead, now + 1_000), exact = true)
    }

    fun meetingKey(eventId: Long, begin: Long) = "$eventId:$begin"

    /** Keeps the announced list short: only meetings that haven't finished yet. */
    fun markAnnounced(context: Context, eventId: Long, begin: Long) {
        val settings = Settings(context)
        val cutoff = System.currentTimeMillis() - 12 * 3_600_000L
        val kept = settings.announcedMeetings.filter { key -> (key.substringAfter(':').toLongOrNull() ?: 0L) > cutoff }
        settings.announcedMeetings = (kept + meetingKey(eventId, begin)).toSet()
    }

    /** Re-reads the calendar regularly so meetings added in other apps still get a heads-up. */
    private fun scheduleSync(context: Context) {
        cancelSlot(context, SLOT_SYNC, ACTION_SYNC)
        val settings = Settings(context)
        if (KillSwitch.detonated || !settings.meetingAlerts || !CalendarRepo.hasPermission(context)) return
        val intent = Intent(context, AlertReceiver::class.java).setAction(ACTION_SYNC)
        setAlarm(context, SLOT_SYNC, intent, System.currentTimeMillis() + SYNC_EVERY_MS, exact = false)
    }

    fun scheduleBrief(context: Context) {
        cancelSlot(context, SLOT_BRIEF, ACTION_BRIEF)
        val settings = Settings(context)
        if (KillSwitch.detonated || !settings.briefEnabled) return
        val zone = ZoneId.systemDefault()
        val time = LocalTime.of(settings.briefMinuteOfDay / 60, settings.briefMinuteOfDay % 60)
        var at = LocalDate.now().atTime(time).atZone(zone)
        if (at.toInstant().toEpochMilli() <= System.currentTimeMillis()) at = at.plusDays(1)
        val intent = Intent(context, AlertReceiver::class.java).setAction(ACTION_BRIEF)
        setAlarm(context, SLOT_BRIEF, intent, at.toInstant().toEpochMilli(), exact = true)
    }

    /** Builds and delivers the brief in the background (it needs the internet and can take a few seconds). */
    fun runBriefNow(context: Context) {
        if (KillSwitch.detonated) return
        val request = OneTimeWorkRequestBuilder<BriefWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(BRIEF_WORK, ExistingWorkPolicy.REPLACE, request)
    }

    fun notify(context: Context, id: Int, title: String, text: String) {
        if (KillSwitch.detonated) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val open = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(context, RileyApp.CHANNEL_REMINDERS)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        context.getSystemService(NotificationManager::class.java).notify(id, notification)
    }

    /** Says [text] out loud if "speak alerts" is on. Safe to call from a receiver or worker. */
    suspend fun speak(context: Context, text: String) {
        if (KillSwitch.detonated || !Settings(context).speakAlerts) return
        val voice = withContext(Dispatchers.Main) { RileyVoice.get(context) }
        withTimeoutOrNull(60_000) { voice.say(text) }
    }

    private fun setAlarm(context: Context, slot: Int, intent: Intent, at: Long, exact: Boolean) {
        val alarms = context.getSystemService(AlarmManager::class.java)
        val operation = PendingIntent.getBroadcast(context, slot, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        if (exact && (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarms.canScheduleExactAlarms())) {
            alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, operation)
        } else {
            alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, operation)
        }
    }

    private fun cancelSlot(context: Context, slot: Int, action: String) {
        pending(context, slot, action, PendingIntent.FLAG_NO_CREATE)?.let {
            context.getSystemService(AlarmManager::class.java).cancel(it)
            it.cancel()
        }
    }

    private fun pending(context: Context, slot: Int, action: String, flags: Int): PendingIntent? =
        PendingIntent.getBroadcast(
            context,
            slot,
            Intent(context, AlertReceiver::class.java).setAction(action),
            flags or PendingIntent.FLAG_IMMUTABLE,
        )
}

class AlertReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (KillSwitch.detonated) return
        val app = context.applicationContext
        when (intent.action) {
            Alerts.ACTION_SYNC -> Alerts.rescheduleAll(app)
            Alerts.ACTION_BRIEF -> {
                Alerts.runBriefNow(app)
                Alerts.scheduleBrief(app)
            }
            Alerts.ACTION_MEETING -> {
                val eventId = intent.getLongExtra(Alerts.EXTRA_EVENT_ID, -1)
                val begin = intent.getLongExtra(Alerts.EXTRA_BEGIN, 0)
                val pending = goAsync()
                CoroutineScope(SupervisorJob() + Dispatchers.Main).launch {
                    try {
                        announceMeeting(app, eventId, begin)
                    } finally {
                        Alerts.scheduleNextMeeting(app)
                        pending.finish()
                    }
                }
            }
        }
    }

    private suspend fun announceMeeting(context: Context, eventId: Long, begin: Long) {
        Alerts.markAnnounced(context, eventId, begin)
        // Re-check: the meeting may have been moved or cancelled since the alarm was set.
        val event = withContext(Dispatchers.IO) {
            CalendarRepo.events(context, begin, begin + 60_000).firstOrNull { it.eventId == eventId && it.begin == begin }
        } ?: return
        val minutes = ((event.begin - System.currentTimeMillis()) / 60_000).coerceAtLeast(0)
        val whenText = if (minutes <= 1) "starting now" else "in $minutes minutes"
        val place = if (event.location.isNotBlank()) " Location: ${event.location}." else ""
        val callName = Settings(context).callName
        Alerts.notify(context, Alerts.NOTIFY_MEETING, "Riley · Meeting $whenText", "${event.title}.$place")
        // Receivers get ~10 seconds; keep the spoken line short.
        withTimeoutOrNull(8_000) { Alerts.speak(context, "Heads up, $callName. ${event.title}, $whenText.$place") }
    }
}
