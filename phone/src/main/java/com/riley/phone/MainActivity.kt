package com.riley.phone

import android.Manifest
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.speech.RecognizerIntent
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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

val Bg = Color(0xFF0F1112)
val Panel = Color(0xFF1B1D1F)
val Accent = Color(0xFF9DB08A)
val TextMain = Color(0xFFE8E6E1)
val TextDim = Color(0xFF8C8F91)
val Danger = Color(0xFFE0645C)

@Composable
fun RileyPhoneTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Accent,
            onPrimary = Color.Black,
            background = Bg,
            onBackground = TextMain,
            surface = Panel,
            onSurface = TextMain,
        ),
        content = content,
    )
}

/** Pairing, status, and a way to talk to Riley whenever you like. */
class MainActivity : ComponentActivity() {
    private lateinit var settings: PhoneSettings
    private lateinit var voice: PhoneVoice

    private var topic by mutableStateOf("")
    private var code by mutableStateOf("")
    private var tablet by mutableStateOf("")
    private var status by mutableStateOf<String?>(null)
    private var transcript by mutableStateOf(listOf<String>())
    private var busy by mutableStateOf(false)

    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val speechLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val heard = result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
        if (result.resultCode == RESULT_OK && !heard.isNullOrBlank()) send(heard)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        settings = PhoneSettings(this)
        voice = PhoneVoice(this)
        topic = settings.topic
        code = settings.code
        tablet = settings.tablet
        askPermissions()
        if (settings.paired) RingService.start(this)

        setContent {
            val connected by RingService.connected.collectAsState()
            RileyPhoneTheme {
                Column(
                    Modifier.fillMaxSize().background(Bg).safeDrawingPadding().padding(16.dp).verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text("RILEY PHONE", color = Accent, fontWeight = FontWeight.Bold, fontSize = 20.sp, letterSpacing = 4.sp)
                    Text(
                        if (connected) "Waiting for Riley's calls." else "Not connected to the relay yet.",
                        color = if (connected) TextDim else Danger,
                    )

                    Text("PAIRING", color = Accent, fontSize = 12.sp, letterSpacing = 3.sp)
                    Text("Copy these three lines from the tablet: Riley → Settings → Calls to my phone.", color = TextDim)
                    OutlinedTextField(
                        value = topic,
                        onValueChange = { topic = it },
                        label = { Text("Channel") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = code,
                        onValueChange = { code = it },
                        label = { Text("Code") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = tablet,
                        onValueChange = { tablet = it },
                        label = { Text("Tablet address, e.g. 192.168.1.20:8787") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Button(onClick = { save() }, modifier = Modifier.weight(1f)) { Text("SAVE & CONNECT") }
                        OutlinedButton(onClick = { test() }, modifier = Modifier.weight(1f)) { Text("TEST") }
                    }
                    status?.let { Text(it, color = if (it.startsWith("Couldn't") || it.startsWith("Failed")) Danger else TextDim) }

                    // Android 14 and up make apps ask before they may open a screen over the lock screen.
                    val needsFullScreenGrant = Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE &&
                        !getSystemService(NotificationManager::class.java).canUseFullScreenIntent()
                    if (needsFullScreenGrant) {
                        Text(
                            "Android won't let Riley open the call screen by itself. Allow full-screen alerts so calls " +
                                "wake the phone properly.",
                            color = Danger,
                        )
                        OutlinedButton(onClick = {
                            runCatching {
                                startActivity(
                                    Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:$packageName")),
                                )
                            }
                        }) { Text("ALLOW FULL-SCREEN ALERTS") }
                    }

                    Text("TALK TO RILEY", color = Accent, fontSize = 12.sp, letterSpacing = 3.sp)
                    Text("Works while your phone and the tablet are on the same Wi-Fi.", color = TextDim)
                    transcript.takeLast(12).forEach { line ->
                        Text(line, color = if (line.startsWith("You:")) TextDim else TextMain)
                        Spacer(Modifier.width(4.dp))
                    }
                    if (busy) Text("Riley is on it…", color = TextDim)
                    Button(onClick = { listen() }, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text("TALK") }

                    Text("KILL SWITCH", color = Accent, fontSize = 12.sp, letterSpacing = 3.sp)
                    Text(
                        "Say \"Code Red: Detonate yourself\" to Riley and this app wipes itself too. You can also wipe " +
                            "it here.",
                        color = TextDim,
                    )
                    OutlinedButton(onClick = { wipe() }) { Text("WIPE RILEY PHONE") }
                }
            }
        }
    }

    override fun onDestroy() {
        if (::voice.isInitialized) voice.shutdown()
        super.onDestroy()
    }

    private fun save() {
        settings.topic = topic
        settings.code = code
        settings.tablet = tablet
        status = if (settings.paired) {
            RingService.stop(this)
            RingService.start(this)
            "Saved. Listening for Riley."
        } else {
            "Couldn't save: the channel and code are both needed."
        }
    }

    private fun test() {
        busy = true
        lifecycleScope.launch {
            status = try {
                RileyLink.ping(this@MainActivity)
            } catch (e: Exception) {
                "Couldn't reach the tablet: ${e.message ?: "no answer"}. Same Wi-Fi? Is Riley's phone link on?"
            }
            busy = false
        }
    }

    private fun listen() {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-IN")
            .putExtra(RecognizerIntent.EXTRA_PROMPT, "Talk to Riley")
        try {
            speechLauncher.launch(intent)
        } catch (e: ActivityNotFoundException) {
            status = "No speech recogniser on this phone."
        }
    }

    private fun send(text: String) {
        transcript = transcript + "You: $text"
        busy = true
        lifecycleScope.launch {
            val reply = try {
                RileyLink.say(this@MainActivity, text)
            } catch (e: Exception) {
                "Can't reach the tablet. ${e.message ?: ""}".trim()
            }
            transcript = transcript + "Riley: $reply"
            busy = false
            voice.say(reply)
        }
    }

    private fun wipe() {
        RingService.stop(this)
        settings.wipe()
        topic = ""
        code = ""
        tablet = ""
        transcript = emptyList()
        status = "Wiped. Uninstall this app to finish."
        runCatching { startActivity(Intent(Intent.ACTION_DELETE, Uri.parse("package:$packageName"))) }
    }

    private fun askPermissions() {
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }
}
