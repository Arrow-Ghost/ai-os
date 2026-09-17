package com.riley.assistant.alerts

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.riley.assistant.brain.Brain
import com.riley.assistant.calendar.CalendarRepo
import com.riley.assistant.data.ChatMessage
import com.riley.assistant.data.Settings
import com.riley.assistant.data.Store
import com.riley.assistant.data.TimeUtil
import com.riley.assistant.killswitch.KillSwitch
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.Locale

/** The morning brief: today's meetings, tasks due or overdue, and anything urgent coming up. */
class BriefWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val context = applicationContext
        if (KillSwitch.detonated) return Result.success()
        val settings = Settings(context)
        // After a kill switch wipe there are no keys: stay silent.
        if (settings.geminiKey.isBlank() && settings.groqKey.isBlank()) return Result.success()

        val facts = gatherFacts(context)
        val brief = Brain(context).compose(
            "It's time for the morning brief. Using only the facts below, brief ${settings.callName} out loud in " +
                "under 90 words: greet them briefly, then meetings in time order, then what's due or overdue, then one " +
                "line on what matters most today. No lists or symbols, it will be spoken.\n\n$facts",
        ) ?: fallback(settings.callName, facts)

        if (KillSwitch.detonated) return Result.success()
        Store.get(context).addChat(ChatMessage("riley", brief))
        Alerts.notify(context, Alerts.NOTIFY_BRIEF, "Riley · Morning brief", brief)
        Alerts.speak(context, brief)
        return Result.success()
    }

    private fun gatherFacts(context: Context): String = buildString {
        val now = System.currentTimeMillis()
        val endOfToday = TimeUtil.startOfDayAfter(1)
        appendLine("Today is ${LocalDate.now().format(DateTimeFormatter.ofPattern("EEEE d MMMM", Locale.ENGLISH))}.")

        if (CalendarRepo.hasPermission(context)) {
            val events = runCatching { CalendarRepo.events(context, TimeUtil.startOfToday(), endOfToday) }.getOrDefault(emptyList())
            if (events.isEmpty()) {
                appendLine("Meetings today: none.")
            } else {
                appendLine("Meetings today:")
                events.forEach { e ->
                    val place = if (e.location.isNotBlank()) " at ${e.location}" else ""
                    appendLine("- ${e.title}, ${TimeUtil.formatRange(e.begin, e.end, e.allDay)}$place")
                }
            }
        } else {
            appendLine("Calendar: not connected.")
        }

        val open = Store.get(context).tasks().filter { !it.done }
        val overdue = open.filter { it.dueAt != null && it.dueAt < now }
        val today = open.filter { it.dueAt != null && it.dueAt in now until endOfToday }
        val urgentSoon = open.filter {
            it.priority == "urgent" && it.dueAt != null && it.dueAt >= endOfToday && it.dueAt < TimeUtil.startOfDayAfter(4)
        }
        if (overdue.isNotEmpty()) appendLine("Overdue: " + overdue.joinToString("; ") { "${it.title} (was due ${TimeUtil.format(it.dueAt!!)})" })
        appendLine(if (today.isEmpty()) "Tasks due today: none." else "Tasks due today: " + today.joinToString("; ") { "${it.title} at ${TimeUtil.formatClock(it.dueAt!!)}" })
        if (urgentSoon.isNotEmpty()) appendLine("Urgent in the next few days: " + urgentSoon.joinToString("; ") { "${it.title} (${TimeUtil.format(it.dueAt!!)})" })
        val undated = open.count { it.dueAt == null }
        if (undated > 0) appendLine("Open tasks without a date: $undated.")
    }

    private fun fallback(callName: String, facts: String): String =
        "Morning, $callName. Comms to the brain are down, so here are the raw facts. " +
            facts.lines().filter { it.isNotBlank() }.joinToString(" ") { it.removePrefix("- ") }
}
