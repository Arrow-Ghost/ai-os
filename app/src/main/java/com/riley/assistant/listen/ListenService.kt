package com.riley.assistant.listen

import android.Manifest
import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.riley.assistant.MainActivity
import com.riley.assistant.R
import com.riley.assistant.RileyApp
import com.riley.assistant.brain.Conversation
import com.riley.assistant.data.Settings
import com.riley.assistant.killswitch.KillSwitch
import com.riley.assistant.voice.RileyVoice
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

enum class ListenState(val label: String) {
    Off("Hands-free off"),
    Starting("Starting hands-free…"),
    Waiting("Listening for \"Riley\""),
    Listening("Listening…"),
    Thinking("Thinking…"),
    Speaking("Speaking…"),
}

/**
 * Hands-free mode. Runs in the foreground (with a notification) so Android lets it keep the microphone:
 * wait for "Riley" → record what you say → transcribe → Riley answers out loud → listen for a follow-up.
 */
class ListenService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var loop: Job? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private val listenNow = AtomicBoolean(false)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP || KillSwitch.detonated) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            return fail("Microphone permission is off.")
        }
        if (!WakeModel.isInstalled(this)) {
            return fail("Download the wake-word model in Settings first.")
        }
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } catch (e: Exception) {
            return fail("Android blocked hands-free: ${e.message}")
        }
        if (intent?.action == ACTION_LISTEN_NOW) listenNow.set(true)
        if (loop?.isActive != true) loop = scope.launch { runLoop() }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        releaseWakeLock()
        _state.value = ListenState.Off
        super.onDestroy()
    }

    private suspend fun runLoop() {
        _state.value = ListenState.Starting
        _lastError.value = null
        acquireWakeLock()
        val voice = withContext(Dispatchers.Main) { RileyVoice.get(this@ListenService) }
        try {
            WakeWordDetector(WakeModel.dir(this)).use { detector ->
                Mic().use { mic -> converse(detector, mic, voice) }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.e(TAG, "Hands-free stopped", e)
            _lastError.value = "Hands-free stopped: ${e.message}"
        } finally {
            releaseWakeLock()
            _state.value = ListenState.Off
            stopSelf()
        }
    }

    private suspend fun converse(detector: WakeWordDetector, mic: Mic, voice: RileyVoice) {
        var followUp = false
        while (!KillSwitch.detonated) {
            var heardWakeWord = false
            if (!followUp) {
                acquireWakeLock() // refresh before each spell of waiting
                _state.value = ListenState.Waiting
                heardWakeWord = mic.awaitWake(detector) { listenNow.getAndSet(false) }
                if (!heardWakeWord) beep(mic)
            }

            _state.value = ListenState.Listening
            val utterance = mic.recordUtterance(if (followUp) FOLLOW_UP_MS else AFTER_WAKE_MS, heardWakeWord)
            followUp = false
            if (utterance == null) continue

            _state.value = ListenState.Thinking
            val heard = try {
                Transcriber.transcribe(this, utterance)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "Transcription failed", e)
                speak(voice, mic, "Didn't catch that. Comms problem.")
                continue
            }
            if (Transcriber.isPhantom(heard)) continue

            val words = heard.lowercase(Locale.ROOT).filter { it in 'a'..'z' }
            val reply = when {
                words in WAKE_ONLY -> "Go ahead, ${Settings(this).callName}."
                words in DISMISS -> {
                    speak(voice, mic, "Copy.")
                    continue
                }
                // null: the kill switch fired, or Riley is still busy with a typed message.
                else -> Conversation.handle(this, heard) ?: continue
            }
            if (KillSwitch.detonated) break

            speak(voice, mic, reply)
            followUp = true
        }
    }

    private suspend fun speak(voice: RileyVoice, mic: Mic, text: String) {
        _state.value = ListenState.Speaking
        voice.say(text)
        delay(200)
        mic.drain()
    }

    private suspend fun beep(mic: Mic) {
        val tone = runCatching { ToneGenerator(AudioManager.STREAM_MUSIC, 60) }.getOrNull() ?: return
        tone.startTone(ToneGenerator.TONE_PROP_BEEP, 120)
        delay(180)
        tone.release()
        mic.drain()
    }

    private fun fail(message: String): Int {
        _lastError.value = message
        _state.value = ListenState.Off
        stopSelf()
        return START_NOT_STICKY
    }

    private fun notification(): Notification {
        val open = PendingIntent.getActivity(
            this,
            1,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this,
            2,
            Intent(this, ListenService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, RileyApp.CHANNEL_LISTENING)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Riley is on comms")
            .setContentText("Say \"Riley\" to talk.")
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(0, "Stop listening", stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    private fun acquireWakeLock() {
        wakeLock?.let {
            if (it.isHeld) it.release()
        }
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Riley:listen")
            .apply {
                setReferenceCounted(false)
                // Timeout as a safety net; the loop refreshes it while hands-free is on.
                acquire(WAKE_LOCK_MS)
            }
    }

    private fun releaseWakeLock() {
        runCatching { wakeLock?.takeIf { it.isHeld }?.release() }
        wakeLock = null
    }

    companion object {
        private const val TAG = "RileyListen"
        private const val NOTIFICATION_ID = 7
        private const val ACTION_STOP = "com.riley.assistant.STOP_LISTENING"
        private const val ACTION_LISTEN_NOW = "com.riley.assistant.LISTEN_NOW"
        private const val AFTER_WAKE_MS = 6_000L
        private const val FOLLOW_UP_MS = 8_000L
        private const val WAKE_LOCK_MS = 6 * 60 * 60_000L

        private val WAKE_ONLY = setOf("riley", "heyriley", "okriley", "okayriley")
        private val DISMISS = setOf(
            "stop", "cancel", "nevermind", "thatsall", "thatsit", "nothing", "standdown",
            "rileystop", "rileystanddown", "goodbye", "bye",
        )

        private val _state = MutableStateFlow(ListenState.Off)
        val state: StateFlow<ListenState> = _state.asStateFlow()

        private val _lastError = MutableStateFlow<String?>(null)
        val lastError: StateFlow<String?> = _lastError.asStateFlow()

        /** Call while Riley's screen is open: Android only allows starting the microphone from the foreground. */
        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, ListenService::class.java))
        }

        /** Skip the wake word and listen right now (the MIC button while hands-free is on). */
        fun listenNow(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, ListenService::class.java).setAction(ACTION_LISTEN_NOW))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, ListenService::class.java))
        }
    }
}
