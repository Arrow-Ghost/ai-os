package com.riley.assistant.link

import android.content.Context
import android.util.Log
import com.riley.assistant.alerts.Alerts
import com.riley.assistant.calendar.CalendarEvent
import com.riley.assistant.data.ChatMessage
import com.riley.assistant.data.Settings
import com.riley.assistant.data.Store
import com.riley.assistant.data.Task
import com.riley.assistant.data.TimeUtil
import com.riley.assistant.killswitch.KillSwitch
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Riley chasing the owner. It rings the phone about the things that can't slip, and if nobody answers
 * it rings again, up to [MAX_ATTEMPTS] times, then writes it off in the chat.
 */
object Escalation {
    private const val MAX_ATTEMPTS = 3
    private const val TAG = "RileyEscalation"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun considerTask(context: Context, task: Task) {
        val settings = Settings(context)
        if (!settings.callsEnabled || !settings.callForUrgentTasks) return
        if (task.priority != "urgent") return
        val due = task.dueAt?.let { " Due ${TimeUtil.formatClock(it)}." }.orEmpty()
        ring(context, "task-${task.id}", "Urgent: ${task.title}", "${task.title}.$due", attempt = 1)
    }

    fun considerMeeting(context: Context, event: CalendarEvent, minutesAway: Long) {
        val settings = Settings(context)
        if (!settings.callsEnabled || !settings.callForMeetings) return
        val whenText = if (minutesAway <= 1) "starting now" else "in $minutesAway minutes"
        val place = if (event.location.isNotBlank()) " Location: ${event.location}." else ""
        ring(
            context,
            "event-${event.eventId}-${event.begin}",
            "Meeting $whenText: ${event.title}",
            "${event.title}, $whenText.$place",
            attempt = 1,
        )
    }

    /** Called when the re-ring alarm fires. */
    fun onAlarm(context: Context) {
        val pending = parse(Settings(context).pendingCall) ?: return
        ring(context, pending.id, pending.headline, pending.spoken, pending.attempt + 1)
    }

    /** The phone answered. Returns a short note for the phone to show. */
    fun acknowledge(context: Context, action: String, minutes: Int): String {
        val settings = Settings(context)
        val pending = parse(settings.pendingCall)
        clear(context)
        if (pending == null) return "Nothing pending."

        val store = Store.get(context)
        val taskId = pending.id.removePrefix("task-").toLongOrNull()
        return when {
            action == "snooze" -> {
                if (taskId != null) {
                    store.task(taskId)?.let { task ->
                        val moved = task.copy(dueAt = System.currentTimeMillis() + minutes * 60_000L)
                        store.updateTask(moved)
                        com.riley.assistant.reminders.Reminders.schedule(context, moved)
                    }
                }
                store.addChat(ChatMessage("riley", "Snoozed \"${pending.headline}\" for $minutes minutes."))
                "Snoozed $minutes minutes."
            }

            taskId != null -> {
                store.task(taskId)?.let { task ->
                    store.updateTask(task.copy(done = true, lastDoneAt = System.currentTimeMillis()))
                    com.riley.assistant.reminders.Reminders.cancel(context, taskId)
                }
                store.addChat(ChatMessage("riley", "Marked done from your phone: ${pending.headline}."))
                "Marked done."
            }

            else -> {
                store.addChat(ChatMessage("riley", "Acknowledged from your phone: ${pending.headline}."))
                "Copy that."
            }
        }
    }

    fun clear(context: Context) {
        Settings(context).pendingCall = ""
        Alerts.cancelEscalation(context)
    }

    private fun ring(context: Context, id: String, headline: String, spoken: String, attempt: Int) {
        if (KillSwitch.detonated) return
        val settings = Settings(context)
        if (attempt > MAX_ATTEMPTS) {
            Store.get(context).addChat(
                ChatMessage("system", "Rang your phone $MAX_ATTEMPTS times about \"$headline\" with no answer."),
            )
            clear(context)
            return
        }
        settings.pendingCall = "$id|$attempt|$headline|$spoken"
        val prefix = if (attempt == 1) "" else "Still waiting, attempt $attempt. "
        scope.launch {
            val failure = Notifier.send(context, Notifier.KIND_CALL, headline, prefix + spoken, id)
            if (failure != null) {
                Log.w(TAG, "could not ring the phone: $failure")
                Store.get(context).addChat(ChatMessage("system", "Couldn't ring your phone: $failure"))
            }
        }
        Alerts.scheduleEscalation(context, System.currentTimeMillis() + settings.escalateMinutes * 60_000L)
    }

    private data class Pending(val id: String, val attempt: Int, val headline: String, val spoken: String)

    private fun parse(raw: String): Pending? {
        val parts = raw.split('|', limit = 4)
        if (parts.size < 4) return null
        val attempt = parts[1].toIntOrNull() ?: return null
        return Pending(parts[0], attempt, parts[2], parts[3])
    }
}
