package com.riley.assistant.link

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.riley.assistant.MainActivity
import com.riley.assistant.R
import com.riley.assistant.RileyApp
import com.riley.assistant.data.Settings
import com.riley.assistant.killswitch.KillSwitch
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Keeps the phone link open while Riley is on duty. */
class LinkService : Service() {
    private var server: LinkServer? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP || KillSwitch.detonated) {
            stopSelf()
            return START_NOT_STICKY
        }
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } catch (e: Exception) {
            Log.w(TAG, "Android blocked the phone link", e)
            stopSelf()
            return START_NOT_STICKY
        }
        if (server == null) {
            val port = Settings(this).linkPort
            server = try {
                LinkServer(applicationContext).also { it.start(port) }
            } catch (e: Exception) {
                Log.w(TAG, "could not open port $port", e)
                _listening.value = false
                stopSelf()
                return START_NOT_STICKY
            }
            _listening.value = true
        }
        return START_STICKY
    }

    override fun onDestroy() {
        server?.close()
        server = null
        _listening.value = false
        super.onDestroy()
    }

    private fun notification(): Notification {
        val open = PendingIntent.getActivity(
            this,
            3,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this,
            4,
            Intent(this, LinkService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, RileyApp.CHANNEL_LISTENING)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Riley phone link")
            .setContentText("Your phone can reach Riley on this Wi-Fi.")
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    companion object {
        private const val TAG = "RileyLinkService"
        private const val NOTIFICATION_ID = 9
        private const val ACTION_STOP = "com.riley.assistant.STOP_LINK"

        private val _listening = MutableStateFlow(false)
        val listening: StateFlow<Boolean> = _listening.asStateFlow()

        fun start(context: Context) {
            if (KillSwitch.detonated || !Settings(context).callsEnabled) return
            ContextCompat.startForegroundService(context, Intent(context, LinkService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, LinkService::class.java))
        }
    }
}
