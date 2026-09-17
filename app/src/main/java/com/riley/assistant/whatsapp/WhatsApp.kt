package com.riley.assistant.whatsapp

import android.app.Notification
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationCompat
import androidx.core.app.RemoteInput
import com.riley.assistant.data.Store
import com.riley.assistant.data.WatchedContact
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

data class IncomingMessage(
    /** Chat name: the contact, or the group. */
    val conversation: String,
    /** Who wrote it (in a group this differs from [conversation]). */
    val sender: String,
    val body: String,
    val isGroup: Boolean,
)

/** A reply box Android gave us on a WhatsApp notification, the same one a smartwatch uses. */
private class ReplyHandle(val action: Notification.Action, val remoteInput: RemoteInput, val postedAt: Long)

/**
 * Riley reads WhatsApp through Android's notifications and replies through the notification's own reply
 * box. That uses your normal WhatsApp account and no unofficial library, so nothing risks your number.
 *
 * Limits of this approach: Riley only sees messages that WhatsApp actually shows a notification for, and it
 * can only reply to a chat that has a recent notification. It cannot start a new chat with someone.
 */
object WhatsApp {
    val PACKAGES = setOf("com.whatsapp", "com.whatsapp.w4b")

    private const val MAX_HANDLES = 30
    private const val HANDLE_TTL_MS = 12 * 60 * 60_000L

    private val handles = ConcurrentHashMap<String, ReplyHandle>()
    private val seen = ArrayDeque<String>()

    fun notificationAccessGranted(context: Context): Boolean {
        val enabled = Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners").orEmpty()
        val mine = ComponentName(context, MessageListener::class.java).flattenToString()
        val mineShort = ComponentName(context, MessageListener::class.java).flattenToShortString()
        return enabled.split(':').any { it == mine || it == mineShort }
    }

    fun openNotificationAccessSettings(context: Context) {
        context.startActivity(
            Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }

    /** Pulls the newest message out of a WhatsApp notification, or null if it isn't a real chat message. */
    fun parse(sbn: StatusBarNotification): IncomingMessage? {
        val notification = sbn.notification ?: return null
        if (notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return null
        if (sbn.isOngoing) return null // "Checking for new messages", backups, calls in progress

        val extras = notification.extras
        val conversation = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim().orEmpty()
        if (conversation.isEmpty() || conversation.equals("WhatsApp", ignoreCase = true)) return null

        val style = runCatching { NotificationCompat.MessagingStyle.extractMessagingStyleFromNotification(notification) }.getOrNull()
        val latest = style?.messages?.lastOrNull()
        val isGroup = style?.isGroupConversation ?: extras.getBoolean("android.isGroupConversation", false)
        val body = (latest?.text ?: extras.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: extras.getCharSequence(Notification.EXTRA_TEXT))
            ?.toString()?.trim().orEmpty()
        if (body.isEmpty() || body.startsWith("Checking for new messages")) return null
        if (body.matches(Regex("\\d+ new messages?", RegexOption.IGNORE_CASE))) return null

        val sender = latest?.person?.name?.toString()?.trim()?.takeIf { it.isNotEmpty() }
            ?: if (isGroup) body.substringBefore(':', "").trim().takeIf { it.isNotEmpty() } ?: conversation else conversation
        return IncomingMessage(conversation, sender, body.removePrefix("$sender:").trim(), isGroup)
    }

    /** True the first time a given message is seen; WhatsApp re-posts its notification often. */
    fun isNew(sbn: StatusBarNotification, message: IncomingMessage): Boolean = synchronized(seen) {
        val key = "${sbn.key}|${message.sender}|${message.body.hashCode()}"
        if (key in seen) return false
        seen.addLast(key)
        while (seen.size > 60) seen.removeFirst()
        true
    }

    /**
     * Keeps the reply box for a chat. Stored for every WhatsApp chat, not only watched ones, so the owner can
     * say "reply to Ravi" about any recent message. Only the reply handle is kept here, never the text.
     */
    fun rememberReplyBox(context: Context, sbn: StatusBarNotification, conversation: String) {
        val action = sbn.notification?.actions?.firstOrNull { action ->
            action.remoteInputs?.any { it.allowFreeFormInput } == true
        } ?: return
        val remoteInput = action.remoteInputs?.firstOrNull { it.allowFreeFormInput } ?: return
        handles[key(conversation)] = ReplyHandle(action, RemoteInput.Builder(remoteInput.resultKey).build(), System.currentTimeMillis())
        if (handles.size > MAX_HANDLES) {
            handles.entries.sortedBy { it.value.postedAt }.take(handles.size - MAX_HANDLES).forEach { handles.remove(it.key) }
        }
    }

    fun canReplyTo(contact: String): Boolean = findHandle(contact) != null

    /** Chats Riley could reply to right now. */
    fun repliableChats(): List<String> = handles.keys.toList()

    /** Sends [text] into the chat's reply box. Returns null on success, or a reason it failed. */
    fun sendReply(context: Context, contact: String, text: String): String? {
        val entry = findHandle(contact) ?: return "no recent WhatsApp notification from \"$contact\" to reply to"
        val handle = entry.value
        if (System.currentTimeMillis() - handle.postedAt > HANDLE_TTL_MS) {
            handles.remove(entry.key)
            return "the last message from \"$contact\" is too old to reply to"
        }
        return try {
            val intent = Intent()
            val results = android.os.Bundle().apply { putCharSequence(handle.remoteInput.resultKey, text) }
            RemoteInput.addResultsToIntent(arrayOf(handle.remoteInput), intent, results)
            // WhatsApp expects the reply action to be marked as a background reply.
            RemoteInput.setResultsSource(intent, RemoteInput.SOURCE_FREE_FORM_INPUT)
            handle.action.actionIntent.send(context, 0, intent)
            null
        } catch (e: Exception) {
            "WhatsApp refused the reply (${e.message})"
        }
    }

    /** The rule for a chat, or null when Riley isn't allowed to read it. */
    fun ruleFor(context: Context, conversation: String, sender: String): WatchedContact? {
        val haystack = "$conversation $sender".lowercase(Locale.ROOT)
        return Store.get(context).contacts().firstOrNull { rule ->
            val needle = rule.pattern.trim().lowercase(Locale.ROOT)
            needle.isNotEmpty() && haystack.contains(needle)
        }
    }

    fun forget() {
        handles.clear()
        synchronized(seen) { seen.clear() }
    }

    private fun findHandle(contact: String): Map.Entry<String, ReplyHandle>? {
        val needle = key(contact)
        return handles.entries.firstOrNull { it.key == needle }
            ?: handles.entries.firstOrNull { it.key.contains(needle) || needle.contains(it.key) }
    }

    private fun key(conversation: String) = conversation.trim().lowercase(Locale.ROOT)
}
