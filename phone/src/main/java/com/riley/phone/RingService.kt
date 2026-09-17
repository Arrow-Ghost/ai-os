package com.riley.phone

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Waits for Riley to call. It holds one long-lived connection to the push relay, which is how the
 * ntfy app itself works, and rings the phone the moment a call arrives.
 */
class RingService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var listener: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        val settings = PhoneSettings(this)
        if (!settings.paired) {
            stopSelf()
            return START_NOT_STICKY
        }
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, statusNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } catch (e: Exception) {
            Log.w(TAG, "Android blocked the listener", e)
            stopSelf()
            return START_NOT_STICKY
        }
        if (listener?.isActive != true) listener = scope.launch { listen(settings) }
        return START_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        _connected.value = false
        super.onDestroy()
    }

    private suspend fun listen(settings: PhoneSettings) {
        var backoffMs = 2_000L
        while (currentCoroutineContext().isActive) {
            try {
                stream(settings)
                backoffMs = 2_000L
            } catch (e: Exception) {
                Log.w(TAG, "connection dropped: ${e.message}")
            }
            _connected.value = false
            delay(backoffMs)
            backoffMs = (backoffMs * 2).coerceAtMost(60_000L)
        }
    }

    /** ntfy streams one JSON object per line and holds the connection open. */
    private suspend fun stream(settings: PhoneSettings) {
        val url = URL("${settings.pushServer}/${settings.topic}/json")
        val connection = url.openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 15_000
            connection.readTimeout = 0 // the relay keeps it open and sends keepalives
            connection.inputStream.bufferedReader().use { reader ->
                _connected.value = true
                while (currentCoroutineContext().isActive) {
                    val line = reader.readLine() ?: break
                    if (line.isBlank()) continue
                    val json = runCatching { JSONObject(line) }.getOrNull() ?: continue
                    if (json.optString("event") != "message") continue
                    handle(settings, json.optString("message"))
                }
            }
        } finally {
            runCatching { connection.disconnect() }
        }
    }

    private fun handle(settings: PhoneSettings, message: String) {
        val call = RileyLink.parse(settings, message)
        if (call == null) {
            Log.w(TAG, "ignoring a message that isn't signed by the paired tablet")
            return
        }
        // Remember where the tablet is now; its Wi-Fi address can change.
        if (call.host.isNotBlank()) settings.tablet = "${call.host}:${call.port}"

        when (call.kind) {
            RileyLink.KIND_CALL -> CallActivity.ring(this, call)
            RileyLink.KIND_ALERT -> notify(call.headline, call.spoken)
            RileyLink.KIND_DETONATE -> detonate()
            else -> Log.w(TAG, "unknown message kind ${call.kind}")
        }
    }

    private fun notify(title: String, text: String) {
        val notification = NotificationCompat.Builder(this, PhoneApp.CHANNEL_CALLS)
            .setSmallIcon(R.drawable.ic_riley_phone)
            .setContentTitle(title.ifBlank { "Riley" })
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(ALERT_ID, notification)
    }

    /** The tablet's kill switch fired: wipe this app too, then offer to uninstall it. */
    private fun detonate() {
        val app = applicationContext
        PhoneSettings(app).wipe()
        app.filesDir.deleteRecursively()
        app.cacheDir.deleteRecursively()
        getSystemService(NotificationManager::class.java).cancelAll()

        val uninstall = Intent(Intent.ACTION_DELETE, Uri.parse("package:${app.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val tap = PendingIntent.getActivity(app, 0, uninstall, PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(app, PhoneApp.CHANNEL_CALLS)
            .setSmallIcon(R.drawable.ic_riley_phone)
            .setContentTitle("Riley Phone wiped")
            .setContentText("Tap to finish uninstalling.")
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(DETONATE_ID, notification)

        runCatching {
            app.packageManager.setComponentEnabledSetting(
                ComponentName(app, BootReceiver::class.java),
                PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
                PackageManager.DONT_KILL_APP,
            )
        }
        stopSelf()
    }

    private fun statusNotification(): Notification {
        val open = PendingIntent.getActivity(
            this,
            1,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this,
            2,
            Intent(this, RingService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, PhoneApp.CHANNEL_STATUS)
            .setSmallIcon(R.drawable.ic_riley_phone)
            .setContentTitle("Waiting for Riley")
            .setContentText("Riley can reach you here.")
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    companion object {
        private const val TAG = "RileyPhone"
        private const val NOTIFICATION_ID = 11
        private const val ALERT_ID = 12
        private const val DETONATE_ID = 13
        private const val ACTION_STOP = "com.riley.phone.STOP"

        private val _connected = MutableStateFlow(false)
        val connected: StateFlow<Boolean> = _connected.asStateFlow()

        fun start(context: Context) {
            if (!PhoneSettings(context).paired) return
            ContextCompat.startForegroundService(context, Intent(context, RingService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, RingService::class.java))
        }
    }
}
