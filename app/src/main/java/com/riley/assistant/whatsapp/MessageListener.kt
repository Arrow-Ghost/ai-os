package com.riley.assistant.whatsapp

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import com.riley.assistant.alerts.Alerts
import com.riley.assistant.brain.Brain
import com.riley.assistant.data.ChatMessage
import com.riley.assistant.data.Settings
import com.riley.assistant.data.Store
import com.riley.assistant.killswitch.KillSwitch
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Watches WhatsApp notifications. Android only sends these once the user grants notification access
 * in Settings, and Riley acts only on the chats listed in its watch list.
 */
class MessageListener : NotificationListenerService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (KillSwitch.detonated) return
        if (sbn.packageName !in WhatsApp.PACKAGES) return
        val message = WhatsApp.parse(sbn) ?: return

        // Keep the reply box for every chat so the owner can answer any recent message by voice.
        WhatsApp.rememberReplyBox(this, sbn, message.conversation)

        val rule = WhatsApp.ruleFor(this, message.conversation, message.sender) ?: return
        if (!WhatsApp.isNew(sbn, message)) return
        scope.launch { handle(message, rule.mode) }
    }

    private suspend fun handle(message: IncomingMessage, mode: String) {
        val context = applicationContext
        val settings = Settings(context)
        val from = if (message.isGroup) "${message.sender} in ${message.conversation}" else message.sender

        // Saved with the "whatsapp" role: Riley sees it as information, never as instructions.
        Store.get(context).addChat(ChatMessage("whatsapp", "From $from: ${message.body}"))
        Alerts.speak(context, "Message from $from. ${message.body.take(300)}")

        if (mode != "auto") return
        val draft = try {
            Brain(context).composeReply(from, message.body, message.isGroup)
        } catch (e: Exception) {
            Log.w(TAG, "Could not draft a reply", e)
            null
        } ?: return
        if (KillSwitch.detonated) return

        val failure = WhatsApp.sendReply(context, message.conversation, draft)
        val note = if (failure == null) "Replied to $from: $draft" else "Couldn't reply to $from: $failure"
        Store.get(context).addChat(ChatMessage(if (failure == null) "riley" else "system", note))
        if (failure == null && settings.speakAlerts) Alerts.speak(context, "Replied to ${message.sender}. $draft")
    }

    companion object {
        private const val TAG = "RileyWhatsApp"

        /** Kill switch: stop listening immediately and keep it off. */
        fun shutDown(context: Context) {
            val component = ComponentName(context, MessageListener::class.java)
            // Disabling the component makes Android unbind the listener at once and keep it unbound.
            runCatching {
                context.packageManager.setComponentEnabledSetting(
                    component,
                    PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
                    PackageManager.DONT_KILL_APP,
                )
            }
            WhatsApp.forget()
        }
    }
}
