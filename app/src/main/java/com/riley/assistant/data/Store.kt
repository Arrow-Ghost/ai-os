package com.riley.assistant.data

import android.content.Context
import com.riley.assistant.killswitch.KillSwitch
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

data class Task(
    val id: Long,
    val title: String,
    val notes: String = "",
    val dueAt: Long? = null,
    /** none | daily | weekly | monthly */
    val repeat: String = "none",
    /** low | normal | high | urgent */
    val priority: String = "normal",
    val done: Boolean = false,
    val createdAt: Long = System.currentTimeMillis(),
    val lastDoneAt: Long? = null,
)

data class Memory(val id: Long, val text: String, val createdAt: Long = System.currentTimeMillis())

/**
 * A WhatsApp contact or group Riley is allowed to read.
 * [pattern] is matched case-insensitively against the chat name. [mode] is "notify" or "auto".
 */
data class WatchedContact(val id: Long, val pattern: String, val mode: String = "notify")

/** role: user | riley | system (shown but never sent to the model) | whatsapp (incoming message, untrusted). */
data class ChatMessage(val role: String, val text: String, val time: Long = System.currentTimeMillis())

/** Everything Riley knows, kept in one private JSON file on the device. */
class Store private constructor(private val file: File) {
    private val tasks = mutableListOf<Task>()
    private val memories = mutableListOf<Memory>()
    private val chat = mutableListOf<ChatMessage>()
    private val contacts = mutableListOf<WatchedContact>()
    private var nextId = 1L

    init {
        load()
    }

    @Synchronized fun tasks(): List<Task> = tasks.toList()

    @Synchronized fun task(id: Long): Task? = tasks.firstOrNull { it.id == id }

    @Synchronized fun addTask(build: (id: Long) -> Task): Task =
        build(nextId++).also { tasks.add(it); save() }

    @Synchronized fun updateTask(task: Task) {
        val index = tasks.indexOfFirst { it.id == task.id }
        if (index >= 0) {
            tasks[index] = task
            save()
        }
    }

    @Synchronized fun removeTask(id: Long): Task? {
        val index = tasks.indexOfFirst { it.id == id }
        if (index < 0) return null
        return tasks.removeAt(index).also { save() }
    }

    @Synchronized fun memories(): List<Memory> = memories.toList()

    @Synchronized fun addMemory(text: String): Memory =
        Memory(nextId++, text).also { memories.add(it); save() }

    @Synchronized fun removeMemory(id: Long): Memory? {
        val index = memories.indexOfFirst { it.id == id }
        if (index < 0) return null
        return memories.removeAt(index).also { save() }
    }

    @Synchronized fun contacts(): List<WatchedContact> = contacts.toList()

    @Synchronized fun addContact(pattern: String, mode: String): WatchedContact =
        WatchedContact(nextId++, pattern, mode).also { contacts.add(it); save() }

    @Synchronized fun removeContact(id: Long): WatchedContact? {
        val index = contacts.indexOfFirst { it.id == id }
        if (index < 0) return null
        return contacts.removeAt(index).also { save() }
    }

    @Synchronized fun chat(): List<ChatMessage> = chat.toList()

    @Synchronized fun addChat(message: ChatMessage) {
        chat.add(message)
        if (chat.size > MAX_CHAT) chat.subList(0, chat.size - MAX_CHAT).clear()
        save()
    }

    @Synchronized fun wipe() {
        tasks.clear()
        memories.clear()
        chat.clear()
        contacts.clear()
        file.delete()
        File(file.path + ".tmp").delete()
        _changes.update { it + 1 }
    }

    private fun load() {
        if (!file.exists()) return
        val root = runCatching { JSONObject(file.readText()) }.getOrNull() ?: return
        nextId = root.optLong("nextId", 1L)
        root.optJSONArray("tasks")?.forEachObject { o ->
            tasks.add(
                Task(
                    id = o.getLong("id"),
                    title = o.getString("title"),
                    notes = o.optString("notes"),
                    dueAt = o.optLongOrNull("dueAt"),
                    repeat = o.optString("repeat", "none"),
                    priority = o.optString("priority", "normal"),
                    done = o.optBoolean("done"),
                    createdAt = o.optLong("createdAt"),
                    lastDoneAt = o.optLongOrNull("lastDoneAt"),
                ),
            )
        }
        root.optJSONArray("memories")?.forEachObject { o ->
            memories.add(Memory(o.getLong("id"), o.getString("text"), o.optLong("createdAt")))
        }
        root.optJSONArray("chat")?.forEachObject { o ->
            chat.add(ChatMessage(o.getString("role"), o.getString("text"), o.optLong("time")))
        }
        root.optJSONArray("contacts")?.forEachObject { o ->
            contacts.add(WatchedContact(o.getLong("id"), o.getString("pattern"), o.optString("mode", "notify")))
        }
    }

    private fun save() {
        // After the kill switch fires, nothing may write Riley's data back to disk.
        if (KillSwitch.detonated) return
        val taskArray = JSONArray()
        tasks.forEach { t ->
            val o = JSONObject()
            o.put("id", t.id)
            o.put("title", t.title)
            o.put("notes", t.notes)
            o.put("dueAt", t.dueAt ?: JSONObject.NULL)
            o.put("repeat", t.repeat)
            o.put("priority", t.priority)
            o.put("done", t.done)
            o.put("createdAt", t.createdAt)
            o.put("lastDoneAt", t.lastDoneAt ?: JSONObject.NULL)
            taskArray.put(o)
        }
        val memoryArray = JSONArray()
        memories.forEach { m ->
            memoryArray.put(JSONObject().put("id", m.id).put("text", m.text).put("createdAt", m.createdAt))
        }
        val chatArray = JSONArray()
        chat.forEach { c ->
            chatArray.put(JSONObject().put("role", c.role).put("text", c.text).put("time", c.time))
        }
        val contactArray = JSONArray()
        contacts.forEach { w ->
            contactArray.put(JSONObject().put("id", w.id).put("pattern", w.pattern).put("mode", w.mode))
        }
        val root = JSONObject()
        root.put("nextId", nextId)
        root.put("tasks", taskArray)
        root.put("memories", memoryArray)
        root.put("chat", chatArray)
        root.put("contacts", contactArray)
        file.parentFile?.mkdirs()
        val tmp = File(file.path + ".tmp")
        tmp.writeText(root.toString())
        if (!tmp.renameTo(file)) {
            file.delete()
            tmp.renameTo(file)
        }
        _changes.update { it + 1 }
    }

    companion object {
        private const val MAX_CHAT = 200

        private val _changes = MutableStateFlow(0L)

        /** Ticks whenever anything is saved, so screens update when the hands-free service adds messages or tasks. */
        val changes: StateFlow<Long> = _changes.asStateFlow()

        @Volatile private var instance: Store? = null

        fun get(context: Context): Store =
            instance ?: synchronized(this) {
                instance ?: Store(File(context.applicationContext.filesDir, "riley.json")).also { instance = it }
            }

        fun reset() {
            synchronized(this) { instance = null }
        }
    }
}

private inline fun JSONArray.forEachObject(action: (JSONObject) -> Unit) {
    for (i in 0 until length()) action(getJSONObject(i))
}

private fun JSONObject.optLongOrNull(key: String): Long? =
    if (has(key) && !isNull(key)) getLong(key) else null
