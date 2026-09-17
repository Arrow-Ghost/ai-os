package com.riley.assistant.killswitch

import android.app.Activity
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.app.NotificationCompat
import com.riley.assistant.R
import com.riley.assistant.RileyApp
import com.riley.assistant.alerts.Alerts
import com.riley.assistant.data.Settings
import com.riley.assistant.data.Store
import com.riley.assistant.listen.ListenService
import com.riley.assistant.reminders.Reminders
import com.riley.assistant.voice.RileyVoice
import com.riley.assistant.whatsapp.MessageListener
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.util.Locale

/**
 * "Code Red: Detonate yourself".
 *
 * Checked with a plain string match on the owner's own typed or spoken words, before anything reaches the
 * AI model. The model can't trigger it, and text from messages or notifications never reaches it.
 */
object KillSwitch {
    private const val CODE = "codereddetonateyourself"

    private val _state = MutableStateFlow(false)
    val state: StateFlow<Boolean> = _state.asStateFlow()

    /** Once true, Riley refuses to save, schedule, listen, speak or run tools for the rest of this process. */
    val detonated: Boolean get() = _state.value

    fun matches(input: String): Boolean {
        val letters = input.lowercase(Locale.ROOT).filter { it in 'a'..'z' }
        return letters.removePrefix("hey").removePrefix("okay").removePrefix("ok").removePrefix("riley") == CODE
    }

    /** Call on the main thread. */
    fun detonate(context: Context) {
        _state.value = true
        val app = context.applicationContext
        val notifications = app.getSystemService(NotificationManager::class.java)

        // 1. Stop all work: speech, the microphone, scheduled reminders, visible alerts.
        RileyVoice.release()
        runCatching { ListenService.stop(app) }
        runCatching { MessageListener.shutDown(app) } // stops reading WhatsApp notifications for good
        runCatching { Reminders.cancelAll(app) }
        runCatching { Alerts.cancelAll(app) } // meeting heads-ups, calendar re-checks, morning brief
        notifications.cancelAll()

        // 2. Delete everything Riley owns: tasks, memory, chat, API keys, settings, downloaded speech model.
        //    Your calendar events belong to your Google account, not Riley, so they are left alone.
        Store.get(app).wipe()
        Store.reset()
        Settings(app).wipe()
        app.filesDir.deleteRecursively()
        app.cacheDir.deleteRecursively()
        File(app.applicationInfo.dataDir, "shared_prefs").deleteRecursively()

        // 3. Ask Android to remove the app. A normal tablet always shows a confirm dialog for this.
        val uninstall = Intent(Intent.ACTION_DELETE, Uri.parse("package:${app.packageName}"))
        if (context is Activity) {
            runCatching { context.startActivity(uninstall) }
        } else {
            // Triggered by voice while Riley's screen may be closed: Android can block opening the dialog from
            // the background, so also leave a one-tap notification for it.
            uninstall.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching { app.startActivity(uninstall) }
            runCatching {
                val tap = PendingIntent.getActivity(app, 0, uninstall, PendingIntent.FLAG_IMMUTABLE)
                val notification = NotificationCompat.Builder(app, RileyApp.CHANNEL_REMINDERS)
                    .setSmallIcon(R.drawable.ic_notify)
                    .setContentTitle("Riley wiped")
                    .setContentText("Tap to finish uninstalling Riley.")
                    .setContentIntent(tap)
                    .setAutoCancel(true)
                    .build()
                notifications.notify(UNINSTALL_NOTIFICATION_ID, notification)
            }
        }
    }

    private const val UNINSTALL_NOTIFICATION_ID = 999
}
