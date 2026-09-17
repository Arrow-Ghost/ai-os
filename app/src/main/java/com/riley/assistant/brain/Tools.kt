package com.riley.assistant.brain

import android.content.ContentValues
import android.content.Context
import android.provider.CalendarContract
import com.riley.assistant.alerts.Alerts
import com.riley.assistant.calendar.CalendarEvent
import com.riley.assistant.calendar.CalendarRepo
import com.riley.assistant.data.Store
import com.riley.assistant.data.Task
import com.riley.assistant.data.TimeUtil
import com.riley.assistant.killswitch.KillSwitch
import com.riley.assistant.reminders.Reminders
import com.riley.assistant.whatsapp.WhatsApp
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.ZoneId
import java.util.Locale

/** A tool the model may call. [parameters] is a JSON schema object. */
data class ToolDef(val name: String, val description: String, val parameters: JSONObject)

private const val NO_CALENDAR = "error: calendar access is off; tell the user to connect the calendar in Riley's Settings"
private val REPEATS = listOf("none", "daily", "weekly", "monthly")
private val PRIORITIES = listOf("low", "normal", "high", "urgent")

object Tools {
    val definitions: List<ToolDef> = listOf(
        ToolDef(
            "add_task",
            "Log a task, deadline or personal reminder. A reminder fires at the due time. " +
                "For meetings or appointments with other people, use add_event instead.",
            schema(
                "title" to prop("string", "Short description, e.g. 'Call the bank about card'"),
                "due" to prop("string", "Local date/time 'yyyy-MM-dd HH:mm' (or 'yyyy-MM-dd'). Omit if no deadline."),
                "repeat" to enumProp(REPEATS, "How often it repeats. Needs a due time. Default none."),
                "priority" to enumProp(PRIORITIES, "Default normal. Use urgent only for things that truly can't slip."),
                "notes" to prop("string", "Extra details: people, place, links."),
                required = listOf("title"),
            ),
        ),
        ToolDef(
            "list_tasks",
            "List the user's tasks with ids, due times, repeat and priority.",
            schema("filter" to enumProp(listOf("pending", "today", "overdue", "done", "all"), "Default pending.")),
        ),
        ToolDef(
            "complete_task",
            "Mark a task done. For a repeating task this logs it and keeps the next occurrence.",
            schema("id" to prop("integer", "Task id"), required = listOf("id")),
        ),
        ToolDef(
            "update_task",
            "Change a task. Only pass the fields that change.",
            schema(
                "id" to prop("integer", "Task id"),
                "title" to prop("string", "New title"),
                "due" to prop("string", "New local date/time 'yyyy-MM-dd HH:mm', or 'none' to remove the deadline."),
                "repeat" to enumProp(REPEATS, "New repeat rule"),
                "priority" to enumProp(PRIORITIES, "New priority"),
                "notes" to prop("string", "New notes"),
                required = listOf("id"),
            ),
        ),
        ToolDef(
            "delete_task",
            "Delete a task permanently. Only when the user asks to remove or cancel it.",
            schema("id" to prop("integer", "Task id"), required = listOf("id")),
        ),
        ToolDef(
            "remember",
            "Save a lasting fact or preference about the user, e.g. 'Prefers meetings after 11am'.",
            schema("fact" to prop("string", "The fact, written in third person"), required = listOf("fact")),
        ),
        ToolDef(
            "forget",
            "Delete a saved memory by id.",
            schema("id" to prop("integer", "Memory id"), required = listOf("id")),
        ),
        ToolDef(
            "list_events",
            "List calendar meetings and events in a time window. Default: now until the end of tomorrow.",
            schema(
                "from" to prop("string", "Local start 'yyyy-MM-dd HH:mm' or 'yyyy-MM-dd'"),
                "to" to prop("string", "Local end 'yyyy-MM-dd HH:mm' or 'yyyy-MM-dd' (exclusive)"),
            ),
        ),
        ToolDef(
            "add_event",
            "Put a meeting or appointment in the user's calendar.",
            schema(
                "title" to prop("string", "e.g. 'Call with Rahul about the pitch'"),
                "start" to prop("string", "Local start 'yyyy-MM-dd HH:mm'"),
                "end" to prop("string", "Local end 'yyyy-MM-dd HH:mm'. Omit to use duration_minutes."),
                "duration_minutes" to prop("integer", "Length if no end is given. Default 60."),
                "location" to prop("string", "Place or meeting link"),
                "notes" to prop("string", "Agenda or details"),
                required = listOf("title", "start"),
            ),
        ),
        ToolDef(
            "update_event",
            "Move, rename or change a calendar event. Confirm with the user first unless they clearly asked for this change.",
            schema(
                "id" to prop("integer", "Event id from list_events"),
                "title" to prop("string", "New title"),
                "start" to prop("string", "New local start 'yyyy-MM-dd HH:mm' (keeps the same length unless end is given)"),
                "end" to prop("string", "New local end 'yyyy-MM-dd HH:mm'"),
                "location" to prop("string", "New location"),
                "notes" to prop("string", "New notes"),
                required = listOf("id"),
            ),
        ),
        ToolDef(
            "delete_event",
            "Remove a calendar event. Only when the user clearly asked to cancel or remove it.",
            schema("id" to prop("integer", "Event id from list_events"), required = listOf("id")),
        ),
        ToolDef(
            "reply_whatsapp",
            "Send a WhatsApp reply to a chat that messaged recently. Only when the user asked you to reply. " +
                "Read the exact wording back to the user afterwards.",
            schema(
                "contact" to prop("string", "Contact or group name as it appears in WhatsApp"),
                "text" to prop("string", "Exactly what to send"),
                required = listOf("contact", "text"),
            ),
        ),
        ToolDef(
            "watch_contact",
            "Start reading WhatsApp messages from a contact or group. mode 'notify' tells the user about them; " +
                "mode 'auto' also lets you answer that contact by yourself. Ask before using 'auto'.",
            schema(
                "name" to prop("string", "Contact or group name, or part of it"),
                "mode" to enumProp(listOf("notify", "auto"), "Default notify."),
                required = listOf("name"),
            ),
        ),
        ToolDef(
            "unwatch_contact",
            "Stop reading messages from a watched contact.",
            schema("name" to prop("string", "Contact name, or part of it"), required = listOf("name")),
        ),
        ToolDef(
            "list_watched_contacts",
            "List the WhatsApp chats Riley is allowed to read, and which ones it answers by itself.",
            schema("unused" to prop("string", "Not used; pass anything or omit.")),
        ),
    )

    private fun prop(type: String, description: String): JSONObject =
        JSONObject().put("type", type).put("description", description)

    private fun enumProp(values: List<String>, description: String): JSONObject =
        prop("string", description).put("enum", JSONArray(values))

    private fun schema(vararg props: Pair<String, JSONObject>, required: List<String> = emptyList()): JSONObject {
        val properties = JSONObject()
        props.forEach { (name, spec) -> properties.put(name, spec) }
        val schema = JSONObject().put("type", "object").put("properties", properties)
        if (required.isNotEmpty()) schema.put("required", JSONArray(required))
        return schema
    }
}

/** Runs tool calls against the local store. Always returns a short text result for the model. */
class ToolExecutor(private val context: Context) {
    private val store = Store.get(context)

    fun run(name: String, args: JSONObject): String {
        if (KillSwitch.detonated) return "error: Riley is offline"
        return try {
            when (name) {
                "add_task" -> addTask(args)
                "list_tasks" -> listTasks(args.optString("filter", "pending"))
                "complete_task" -> completeTask(args.idArg())
                "update_task" -> updateTask(args)
                "delete_task" -> deleteTask(args.idArg())
                "remember" -> remember(args.getString("fact"))
                "forget" -> forget(args.idArg())
                "list_events" -> listEvents(args)
                "add_event" -> addEvent(args)
                "update_event" -> updateEvent(args)
                "delete_event" -> deleteEvent(args.idArg())
                "reply_whatsapp" -> replyWhatsApp(args.getString("contact"), args.getString("text"))
                "watch_contact" -> watchContact(args.getString("name"), args.optString("mode", "notify"))
                "unwatch_contact" -> unwatchContact(args.getString("name"))
                "list_watched_contacts" -> listWatchedContacts()
                else -> "error: unknown tool $name"
            }
        } catch (e: Exception) {
            "error: ${e.message}"
        }
    }

    private fun addTask(args: JSONObject): String {
        val title = args.getString("title").trim()
        if (title.isEmpty()) return "error: title is empty"
        val now = System.currentTimeMillis()
        val repeat = args.optStringOrNull("repeat")?.takeIf { it in REPEATS } ?: "none"
        val priority = args.optStringOrNull("priority")?.takeIf { it in PRIORITIES } ?: "normal"
        var due = args.optStringOrNull("due")?.let {
            TimeUtil.parse(it) ?: return "error: could not read due '$it', use yyyy-MM-dd HH:mm"
        }
        if (repeat != "none") {
            due = TimeUtil.advancePast(due ?: return "error: a repeating task needs a due time", repeat, now)
        }
        val task = store.addTask { id ->
            Task(id = id, title = title, notes = args.optStringOrNull("notes").orEmpty(), dueAt = due, repeat = repeat, priority = priority)
        }
        Reminders.schedule(context, task)
        val warning = if (due != null && due < now) " (warning: that time has already passed)" else ""
        return "ok: added ${describe(task)}$warning"
    }

    private fun listTasks(filter: String): String {
        val now = System.currentTimeMillis()
        val endOfToday = LocalDate.now().plusDays(1).atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli()
        val matching = store.tasks().filter { t ->
            when (filter) {
                "done" -> t.done
                "all" -> true
                "today" -> !t.done && t.dueAt != null && t.dueAt < endOfToday
                "overdue" -> !t.done && t.dueAt != null && t.dueAt < now
                else -> !t.done
            }
        }.sortedBy { it.dueAt ?: Long.MAX_VALUE }
        if (matching.isEmpty()) return "no tasks ($filter)"
        return matching.take(50).joinToString("\n") { describe(it) }
    }

    private fun completeTask(id: Long): String {
        val task = store.task(id) ?: return "error: no task #$id"
        val now = System.currentTimeMillis()
        if (task.repeat != "none" && task.dueAt != null) {
            val updated = task.copy(dueAt = TimeUtil.advancePast(task.dueAt, task.repeat, now), lastDoneAt = now)
            store.updateTask(updated)
            Reminders.schedule(context, updated)
            return "ok: logged ${describe(updated)}"
        }
        val updated = task.copy(done = true, lastDoneAt = now)
        store.updateTask(updated)
        Reminders.cancel(context, id)
        return "ok: completed ${describe(updated)}"
    }

    private fun updateTask(args: JSONObject): String {
        val id = args.idArg()
        var task = store.task(id) ?: return "error: no task #$id"
        args.optStringOrNull("title")?.let { task = task.copy(title = it) }
        args.optStringOrNull("notes")?.let { task = task.copy(notes = it) }
        args.optStringOrNull("repeat")?.takeIf { it in REPEATS }?.let { task = task.copy(repeat = it) }
        args.optStringOrNull("priority")?.takeIf { it in PRIORITIES }?.let { task = task.copy(priority = it) }
        args.optStringOrNull("due")?.let { text ->
            task = if (text.equals("none", ignoreCase = true)) {
                task.copy(dueAt = null, repeat = "none")
            } else {
                task.copy(dueAt = TimeUtil.parse(text) ?: return "error: could not read due '$text', use yyyy-MM-dd HH:mm")
            }
        }
        if (task.repeat != "none") {
            val due = task.dueAt ?: return "error: a repeating task needs a due time"
            task = task.copy(dueAt = TimeUtil.advancePast(due, task.repeat, System.currentTimeMillis()))
        }
        store.updateTask(task)
        Reminders.schedule(context, task)
        return "ok: updated ${describe(task)}"
    }

    private fun deleteTask(id: Long): String {
        val task = store.removeTask(id) ?: return "error: no task #$id"
        Reminders.cancel(context, id)
        return "ok: deleted ${describe(task)}"
    }

    private fun remember(fact: String): String {
        val text = fact.trim()
        if (text.isEmpty()) return "error: nothing to remember"
        store.memories().firstOrNull { it.text.equals(text, ignoreCase = true) }?.let {
            return "ok: already known as memory ${it.id}"
        }
        return "ok: remembered as memory ${store.addMemory(text).id}"
    }

    private fun forget(id: Long): String {
        val memory = store.removeMemory(id) ?: return "error: no memory $id"
        return "ok: forgot '${memory.text}'"
    }

    private fun listEvents(args: JSONObject): String {
        if (!CalendarRepo.hasPermission(context)) return NO_CALENDAR
        val from = args.optStringOrNull("from")?.let { TimeUtil.parse(it) ?: return "error: could not read from '$it'" }
            ?: System.currentTimeMillis()
        val to = args.optStringOrNull("to")?.let { TimeUtil.parse(it) ?: return "error: could not read to '$it'" }
            ?: TimeUtil.startOfDayAfter(2)
        if (to <= from) return "error: 'to' must be after 'from'"
        val events = CalendarRepo.events(context, from, to)
        if (events.isEmpty()) return "no events between ${TimeUtil.format(from)} and ${TimeUtil.format(to)}"
        return events.take(40).joinToString("\n") { describeEvent(it) }
    }

    private fun addEvent(args: JSONObject): String {
        if (!CalendarRepo.hasPermission(context)) return NO_CALENDAR
        val title = args.getString("title").trim()
        if (title.isEmpty()) return "error: title is empty"
        val startText = args.getString("start")
        val start = TimeUtil.parse(startText) ?: return "error: could not read start '$startText'"
        val end = args.optStringOrNull("end")?.let { TimeUtil.parse(it) ?: return "error: could not read end '$it'" }
            ?: (start + args.optLong("duration_minutes", 60L).coerceIn(5L, 24 * 60L) * 60_000L)
        if (end <= start) return "error: end must be after start"

        val clashes = CalendarRepo.events(context, start, end).filter { !it.allDay }
        val id = CalendarRepo.addEvent(
            context,
            title,
            start,
            end,
            args.optStringOrNull("location").orEmpty(),
            args.optStringOrNull("notes").orEmpty(),
        )
        Alerts.scheduleNextMeeting(context)
        val clashText = if (clashes.isEmpty()) "" else " (clashes with: ${clashes.joinToString("; ") { it.title }})"
        return "ok: added event $id \"$title\" ${TimeUtil.formatRange(start, end, false)}$clashText"
    }

    private fun updateEvent(args: JSONObject): String {
        if (!CalendarRepo.hasPermission(context)) return NO_CALENDAR
        val id = args.idArg()
        val event = CalendarRepo.event(context, id) ?: return "error: no event $id"
        if (event.recurring) return "error: event $id is a repeating series; the user should change it in their calendar app"
        if (event.allDay) return "error: event $id is an all-day event; the user should change it in their calendar app"

        val values = ContentValues()
        args.optStringOrNull("title")?.let { values.put(CalendarContract.Events.TITLE, it) }
        args.optStringOrNull("location")?.let { values.put(CalendarContract.Events.EVENT_LOCATION, it) }
        args.optStringOrNull("notes")?.let { values.put(CalendarContract.Events.DESCRIPTION, it) }
        val newStart = args.optStringOrNull("start")?.let { TimeUtil.parse(it) ?: return "error: could not read start '$it'" }
        val newEnd = args.optStringOrNull("end")?.let { TimeUtil.parse(it) ?: return "error: could not read end '$it'" }
        if (newStart != null || newEnd != null) {
            val start = newStart ?: event.begin
            val end = newEnd ?: (start + (event.end - event.begin))
            if (end <= start) return "error: end must be after start"
            values.put(CalendarContract.Events.DTSTART, start)
            values.put(CalendarContract.Events.DTEND, end)
        }
        if (values.size() == 0) return "error: nothing to change"
        if (!CalendarRepo.updateEvent(context, id, values)) return "error: the calendar refused the change"
        Alerts.scheduleNextMeeting(context)
        return "ok: updated ${CalendarRepo.event(context, id)?.let { describeEvent(it) } ?: "event $id"}"
    }

    private fun deleteEvent(id: Long): String {
        if (!CalendarRepo.hasPermission(context)) return NO_CALENDAR
        val event = CalendarRepo.event(context, id) ?: return "error: no event $id"
        if (event.recurring) return "error: event $id is a repeating series; the user should remove it in their calendar app"
        if (!CalendarRepo.deleteEvent(context, id)) return "error: the calendar refused to delete it"
        Alerts.scheduleNextMeeting(context)
        return "ok: deleted \"${event.title}\" (${TimeUtil.formatRange(event.begin, event.end, event.allDay)})"
    }

    private fun replyWhatsApp(contact: String, text: String): String {
        if (!WhatsApp.notificationAccessGranted(context)) {
            return "error: WhatsApp access is off; tell the user to switch on notification access in Riley's Settings"
        }
        val body = text.trim()
        if (body.isEmpty()) return "error: nothing to send"
        val failure = WhatsApp.sendReply(context, contact.trim(), body)
            ?: return "ok: sent to $contact: \"$body\""
        val chats = WhatsApp.repliableChats()
        val hint = if (chats.isEmpty()) "" else " Chats that can be replied to now: ${chats.joinToString(", ")}."
        return "error: $failure.$hint"
    }

    private fun watchContact(name: String, mode: String): String {
        val pattern = name.trim()
        if (pattern.length < 2) return "error: name is too short"
        val wanted = if (mode == "auto") "auto" else "notify"
        store.contacts().firstOrNull { it.pattern.equals(pattern, ignoreCase = true) }?.let { existing ->
            if (existing.mode == wanted) return "ok: already watching ${existing.pattern} ($wanted)"
            store.removeContact(existing.id)
        }
        val rule = store.addContact(pattern, wanted)
        val access = if (WhatsApp.notificationAccessGranted(context)) {
            ""
        } else {
            " (warning: WhatsApp access is still off in Riley's Settings, so nothing will come through yet)"
        }
        val what = if (wanted == "auto") "reading and answering" else "reading"
        return "ok: now $what messages from ${rule.pattern}$access"
    }

    private fun unwatchContact(name: String): String {
        val needle = name.trim().lowercase(Locale.ROOT)
        val rule = store.contacts().firstOrNull { it.pattern.lowercase(Locale.ROOT).contains(needle) }
            ?: return "error: not watching anyone matching \"$name\""
        store.removeContact(rule.id)
        return "ok: stopped reading messages from ${rule.pattern}"
    }

    private fun listWatchedContacts(): String {
        val rules = store.contacts()
        if (rules.isEmpty()) return "no WhatsApp chats are being read"
        return rules.joinToString("\n") { "${it.pattern} (${if (it.mode == "auto") "Riley answers by itself" else "tells the user"})" }
    }

    private fun describeEvent(e: CalendarEvent): String = buildString {
        append("event ${e.eventId} \"${e.title}\" ${TimeUtil.formatRange(e.begin, e.end, e.allDay)}")
        if (e.location.isNotBlank()) append(" at ${e.location}")
        if (e.recurring) append(" (repeating)")
    }

    private fun describe(t: Task): String = buildString {
        append("#${t.id} \"${t.title}\"")
        t.dueAt?.let { append(" due ${TimeUtil.format(it)}") }
        if (t.repeat != "none") append(", repeats ${t.repeat}")
        if (t.priority != "normal") append(", priority ${t.priority}")
        if (t.done) append(", done")
        if (t.notes.isNotBlank()) append(", notes: ${t.notes}")
    }
}

private fun JSONObject.optStringOrNull(key: String): String? =
    if (has(key) && !isNull(key)) getString(key).trim().takeIf { it.isNotEmpty() } else null

private fun JSONObject.idArg(): Long {
    val id = when (val value = opt("id")) {
        is Number -> value.toLong()
        is String -> value.trim().removePrefix("#").toLongOrNull()
        else -> null
    }
    return id ?: throw IllegalArgumentException("missing or invalid id")
}
