package com.riley.assistant

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager

class RileyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        val notifications = getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL_REMINDERS, "Reminders", NotificationManager.IMPORTANCE_HIGH)
                .apply { description = "Task, deadline and meeting reminders from Riley" },
        )
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL_LISTENING, "Hands-free", NotificationManager.IMPORTANCE_LOW)
                .apply { description = "Shown while Riley is listening for its name" },
        )
    }

    companion object {
        const val CHANNEL_REMINDERS = "reminders"
        const val CHANNEL_LISTENING = "listening"
    }
}
