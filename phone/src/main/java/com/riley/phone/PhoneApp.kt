package com.riley.phone

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.media.AudioAttributes
import android.provider.Settings

class PhoneApp : Application() {
    override fun onCreate() {
        super.onCreate()
        val notifications = getSystemService(NotificationManager::class.java)

        // Calls ring on the alarm stream, so they get through silent mode.
        val callChannel = NotificationChannel(CHANNEL_CALLS, "Calls from Riley", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Urgent meetings and deadlines Riley is chasing you about"
            setBypassDnd(true)
            enableVibration(true)
            setSound(
                Settings.System.DEFAULT_ALARM_ALERT_URI,
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build(),
            )
        }
        notifications.createNotificationChannel(callChannel)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL_STATUS, "Riley status", NotificationManager.IMPORTANCE_LOW)
                .apply { description = "Shown while Riley Phone is waiting for calls" },
        )
    }

    companion object {
        const val CHANNEL_CALLS = "calls"
        const val CHANNEL_STATUS = "status"
    }
}
