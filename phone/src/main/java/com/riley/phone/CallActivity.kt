package com.riley.phone

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.speech.RecognizerIntent
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.app.NotificationCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

/** The incoming-call screen: rings over the lock screen, then hands over to a conversation. */
class CallActivity : ComponentActivity() {
    private var ringtone: MediaPlayer? = null
    private lateinit var voice: PhoneVoice

    private var headline by mutableStateOf("Riley is calling")
    private var spoken by mutableStateOf("")
    private var answered by mutableStateOf(false)
    private var busy by mutableStateOf(false)
    private var transcript by mutableStateOf(listOf<String>())

    private val speechLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val heard = result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
        if (result.resultCode == RESULT_OK && !heard.isNullOrBlank()) send(heard)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        voice = PhoneVoice(this)
        readIntent(intent)
        startRinging()

        setContent {
            RileyPhoneTheme {
                Column(
                    Modifier.fillMaxSize().background(Bg).safeDrawingPadding().padding(24.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    Text("RILEY", color = Accent, fontWeight = FontWeight.Bold, fontSize = 22.sp, letterSpacing = 6.sp)
                    Text(headline, color = TextMain, fontSize = 26.sp)
                    if (!answered) {
                        Spacer(Modifier.weight(1f))
                        Button(onClick = { answer() }, modifier = Modifier.fillMaxWidth()) { Text("ANSWER") }
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            OutlinedButton(onClick = { acknowledge("snooze") }, modifier = Modifier.weight(1f)) {
                                Text("SNOOZE 10 MIN")
                            }
                            OutlinedButton(onClick = { acknowledge("done") }, modifier = Modifier.weight(1f)) {
                                Text("DONE")
                            }
                        }
                    } else {
                        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())) {
                            transcript.forEach { line ->
                                Text(line, color = if (line.startsWith("You:")) TextDim else TextMain, fontSize = 17.sp)
                                Spacer(Modifier.width(8.dp))
                            }
                            if (busy) Text("Riley is on it…", color = TextDim)
                        }
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Button(onClick = { listen() }, enabled = !busy, modifier = Modifier.weight(1f)) { Text("TALK") }
                            OutlinedButton(onClick = { hangUp() }, modifier = Modifier.weight(1f)) { Text("END") }
                        }
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        readIntent(intent)
        if (!answered) startRinging()
    }

    override fun onDestroy() {
        stopRinging()
        voice.shutdown()
        super.onDestroy()
    }

    private fun readIntent(intent: Intent) {
        headline = intent.getStringExtra(EXTRA_HEADLINE)?.takeIf { it.isNotBlank() } ?: "Riley is calling"
        spoken = intent.getStringExtra(EXTRA_SPOKEN).orEmpty()
    }

    private fun answer() {
        stopRinging()
        answered = true
        getSystemService(NotificationManager::class.java).cancel(CALL_NOTIFICATION_ID)
        val opening = spoken.ifBlank { headline }
        transcript = transcript + "Riley: $opening"
        lifecycleScope.launch { voice.say(opening) }
    }

    private fun listen() {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-IN")
            .putExtra(RecognizerIntent.EXTRA_PROMPT, "Talk to Riley")
        try {
            speechLauncher.launch(intent)
        } catch (e: ActivityNotFoundException) {
            transcript = transcript + "Riley: No speech recogniser on this phone."
        }
    }

    private fun send(text: String) {
        transcript = transcript + "You: $text"
        busy = true
        lifecycleScope.launch {
            val reply = try {
                RileyLink.say(this@CallActivity, text)
            } catch (e: Exception) {
                "Can't reach the tablet. ${e.message ?: ""}".trim()
            }
            transcript = transcript + "Riley: $reply"
            busy = false
            voice.say(reply)
        }
    }

    private fun acknowledge(action: String) {
        stopRinging()
        busy = true
        lifecycleScope.launch {
            val note = try {
                RileyLink.acknowledge(this@CallActivity, action)
            } catch (e: Exception) {
                "Couldn't reach the tablet, so it may ring again."
            }
            busy = false
            voice.say(note)
            finish()
        }
    }

    private fun hangUp() {
        stopRinging()
        finish()
    }

    private fun startRinging() {
        stopRinging()
        runCatching {
            val uri = RingtoneManager.getActualDefaultRingtoneUri(this, RingtoneManager.TYPE_RINGTONE)
                ?: android.provider.Settings.System.DEFAULT_ALARM_ALERT_URI
            ringtone = MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM) // alarm stream: gets through silent mode
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build(),
                )
                setDataSource(this@CallActivity, uri)
                isLooping = true
                prepare()
                start()
            }
        }.onFailure { Log.w(TAG, "could not ring", it) }
        runCatching {
            val pattern = longArrayOf(0, 800, 700)
            getSystemService(Vibrator::class.java)?.vibrate(VibrationEffect.createWaveform(pattern, 0))
        }
    }

    private fun stopRinging() {
        runCatching {
            ringtone?.stop()
            ringtone?.release()
        }
        ringtone = null
        runCatching { getSystemService(Vibrator::class.java)?.cancel() }
    }

    companion object {
        private const val TAG = "RileyCall"
        private const val CALL_NOTIFICATION_ID = 14
        private const val EXTRA_HEADLINE = "headline"
        private const val EXTRA_SPOKEN = "spoken"

        /**
         * Rings the phone. A full-screen notification is the reliable route: Android lets that open the
         * call screen even from the background, and it still alerts if that permission is switched off.
         */
        fun ring(context: Context, call: RileyCall) {
            val screen = Intent(context, CallActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra(EXTRA_HEADLINE, call.headline)
                .putExtra(EXTRA_SPOKEN, call.spoken)
            val fullScreen = PendingIntent.getActivity(
                context,
                20,
                screen,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val notification = NotificationCompat.Builder(context, PhoneApp.CHANNEL_CALLS)
                .setSmallIcon(R.drawable.ic_riley_phone)
                .setContentTitle("Riley is calling")
                .setContentText(call.headline)
                .setStyle(NotificationCompat.BigTextStyle().bigText(call.spoken.ifBlank { call.headline }))
                .setPriority(NotificationCompat.PRIORITY_MAX)
                .setCategory(NotificationCompat.CATEGORY_CALL)
                .setOngoing(true)
                .setContentIntent(fullScreen)
                .setFullScreenIntent(fullScreen, true)
                .build()
            context.getSystemService(NotificationManager::class.java).notify(CALL_NOTIFICATION_ID, notification)
            runCatching { context.startActivity(screen) }
        }
    }
}
