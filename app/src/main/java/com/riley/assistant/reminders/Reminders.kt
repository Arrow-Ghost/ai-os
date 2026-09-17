package com.riley.assistant.reminders

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
import com.riley.assistant.MainActivity
import com.riley.assistant.R
import com.riley.assistant.RileyApp
import com.riley.assistant.alerts.Alerts
import com.riley.assistant.data.Settings
import com.riley.assistant.data.Store
import com.riley.assistant.data.Task
import com.riley.assistant.data.TimeUtil
import com.riley.assistant.killswitch.KillSwitch
import com.riley.assistant.link.Escalation
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

object Reminders {
    private const val ACTION_REMIND = "com.riley.assistant.REMIND"
    const val EXTRA_TASK_ID = "task_id"

    fun schedule(context: Context, task: Task) {
        cancel(context, task.id)
        val due = task.dueAt ?: return
        if (task.done || due <= System.currentTimeMillis() || KillSwitch.detonated) return
        val alarms = context.getSystemService(AlarmManager::class.java)
        val intent = pendingIntent(context, task.id, PendingIntent.FLAG_UPDATE_CURRENT) ?: return
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarms.canScheduleExactAlarms()) {
            alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, due, intent)
        } else {
            alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, due, intent)
        }
    }

    fun cancel(context: Context, taskId: Long) {
        val intent = pendingIntent(context, taskId, PendingIntent.FLAG_NO_CREATE) ?: return
        context.getSystemService(AlarmManager::class.java).cancel(intent)
        intent.cancel()
    }

    fun cancelAll(context: Context) {
        Store.get(context).tasks().forEach { cancel(context, it.id) }
    }

    /** After reboot or app update: roll repeating tasks forward and re-arm every future alarm. */
    fun rescheduleAll(context: Context) {
        if (KillSwitch.detonated) return
        val store = Store.get(context)
        val now = System.currentTimeMillis()
        store.tasks().forEach { original ->
            var task = original
            val due = task.dueAt
            if (!task.done && task.repeat != "none" && due != null && due <= now) {
                task = task.copy(dueAt = TimeUtil.advancePast(due, task.repeat, now))
                store.updateTask(task)
            }
            schedule(context, task)
        }
    }

    fun notify(context: Context, task: Task) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return

        val openApp = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val important = task.priority == "urgent" || task.priority == "high"
        val body = listOf(task.title, task.notes).filter { it.isNotBlank() }.joinToString("\n")
        val notification = NotificationCompat.Builder(context, RileyApp.CHANNEL_REMINDERS)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(if (important) "Riley · ${task.priority.uppercase()}" else "Riley")
            .setContentText(task.title)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setContentIntent(openApp)
            .setAutoCancel(true)
            .build()
        context.getSystemService(NotificationManager::class.java).notify(task.id.toInt(), notification)
    }

    private fun pendingIntent(context: Context, taskId: Long, flags: Int): PendingIntent? =
        PendingIntent.getBroadcast(
            context,
            taskId.toInt(),
            Intent(context, ReminderReceiver::class.java).setAction(ACTION_REMIND).putExtra(EXTRA_TASK_ID, taskId),
            flags or PendingIntent.FLAG_IMMUTABLE,
        )
}

class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (KillSwitch.detonated) return
        val store = Store.get(context)
        val task = store.task(intent.getLongExtra(Reminders.EXTRA_TASK_ID, -1)) ?: return
        if (task.done) return
        Reminders.notify(context, task)
        val due = task.dueAt
        if (task.repeat != "none" && due != null) {
            val next = task.copy(dueAt = TimeUtil.advancePast(due, task.repeat, System.currentTimeMillis()))
            store.updateTask(next)
            Reminders.schedule(context, next)
        }

        val app = context.applicationContext
        Escalation.considerTask(app, task)
        val callName = Settings(app).callName
        val line = if (task.priority == "urgent") "Urgent, $callName. ${task.title}." else "Reminder, $callName. ${task.title}."
        val pending = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.Main).launch {
            try {
                // Receivers get ~10 seconds, so keep it to one short line.
                withTimeoutOrNull(8_000) { Alerts.speak(app, line) }
            } finally {
                pending.finish()
            }
        }
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            AlarmManager.ACTION_SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED,
            -> {
                Reminders.rescheduleAll(context)
                Alerts.rescheduleAll(context)
            }
        }
    }
}
