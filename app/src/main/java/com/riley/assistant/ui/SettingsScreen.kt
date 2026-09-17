package com.riley.assistant.ui

import android.Manifest
import android.app.AlarmManager
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.speech.tts.TextToSpeech
import android.speech.tts.Voice
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import com.riley.assistant.alerts.Alerts
import com.riley.assistant.calendar.CalendarInfo
import com.riley.assistant.calendar.CalendarRepo
import com.riley.assistant.data.Settings
import com.riley.assistant.data.Store
import com.riley.assistant.link.LinkService
import com.riley.assistant.link.Notifier
import com.riley.assistant.listen.ModelStatus
import com.riley.assistant.listen.WakeModel
import com.riley.assistant.voice.RileyVoice
import com.riley.assistant.whatsapp.WhatsApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun SettingsScreen(settings: Settings, voice: RileyVoice, modifier: Modifier = Modifier) {
    Column(
        modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        BrainSection(settings)
        VoiceSection(settings, voice)
        HandsFreeSection()
        CalendarSection(settings)
        WhatsAppSection()
        BriefSection(settings)
        PhoneSection(settings)
        RemindersSection(settings)

        SectionTitle("KILL SWITCH")
        Text(
            "Type or say \"Code Red: Detonate yourself\" (hands-free works too). Riley stops listening and all work, " +
                "cancels every reminder, erases tasks, memory, chat, API keys and the speech model, then opens " +
                "Android's uninstall prompt.",
            color = TextDim,
        )
    }
}

@Composable
private fun BrainSection(settings: Settings) {
    var callName by rememberSaveable { mutableStateOf(settings.callName) }
    var geminiKey by rememberSaveable { mutableStateOf(settings.geminiKey) }
    var geminiModel by rememberSaveable { mutableStateOf(settings.geminiModel) }
    var groqKey by rememberSaveable { mutableStateOf(settings.groqKey) }
    var groqModel by rememberSaveable { mutableStateOf(settings.groqModel) }
    var saved by remember { mutableStateOf(false) }

    SectionTitle("IDENTITY")
    Field("What Riley calls you", callName) { callName = it; saved = false }

    SectionTitle("BRAIN")
    Field("Gemini API key", geminiKey, secret = true) { geminiKey = it; saved = false }
    Field("Gemini model", geminiModel) { geminiModel = it; saved = false }
    Field("Groq API key (hands-free speech + backup brain)", groqKey, secret = true) { groqKey = it; saved = false }
    Field("Groq model", groqModel) { groqModel = it; saved = false }
    Button(onClick = {
        settings.callName = callName
        settings.geminiKey = geminiKey
        settings.geminiModel = geminiModel
        settings.groqKey = groqKey
        settings.groqModel = groqModel
        saved = true
    }) { Text(if (saved) "SAVED" else "SAVE") }
}

@Composable
private fun VoiceSection(settings: Settings, voice: RileyVoice) {
    var engine by remember { mutableStateOf(settings.ttsEngine) }
    var engines by remember { mutableStateOf(emptyList<TextToSpeech.EngineInfo>()) }
    var voices by remember { mutableStateOf(emptyList<Voice>()) }
    var voiceName by remember { mutableStateOf(settings.voiceName) }
    var pitch by remember { mutableFloatStateOf(settings.pitch) }
    var rate by remember { mutableFloatStateOf(settings.rate) }

    LaunchedEffect(engine) {
        while (!voice.isReady) delay(250)
        engines = voice.engines()
        voices = voice.englishVoices()
        voiceName = settings.voiceName.ifBlank { voice.currentVoiceName() }
        engine = settings.ttsEngine // falls back to "" if the chosen engine failed to start
    }

    SectionTitle("VOICE ENGINE")
    Text(
        "For the proper Riley sound, install the free sherpa-onnx \"northern English male\" voice engine " +
            "(see README), pick it here and set pitch to 1.0.",
        color = TextDim,
    )
    ChoiceRow("Tablet default", selected = engine.isBlank()) {
        voice.setEngine("")
        engine = ""
        voices = emptyList()
    }
    engines.forEach { info ->
        ChoiceRow(info.label, selected = engine == info.name) {
            voice.setEngine(info.name)
            engine = info.name
            voices = emptyList()
        }
    }

    SectionTitle("VOICE")
    if (voices.isEmpty()) {
        Text(
            "No English voices listed by this engine yet. For Google's engine: Android Settings → Accessibility → " +
                "Text-to-speech → install English (United Kingdom).",
            color = TextDim,
        )
    }
    voices.forEach { v ->
        val where = if (v.isNetworkConnectionRequired) "needs internet" else "offline"
        ChoiceRow("${v.name}  (${v.locale.displayCountry.ifBlank { v.locale.displayLanguage }}, $where)", selected = v.name == voiceName) {
            voiceName = v.name
            settings.voiceName = v.name
            voice.applySettings()
            voice.speak("Riley. Comms check.")
        }
    }
    Text("Pitch ${"%.2f".format(pitch)} (lower = deeper; neural voices sound best at 1.0)", color = TextDim)
    Slider(
        value = pitch,
        onValueChange = { pitch = it },
        onValueChangeFinished = { settings.pitch = pitch; voice.applySettings() },
        valueRange = 0.5f..1.2f,
    )
    Text("Speed ${"%.2f".format(rate)}", color = TextDim)
    Slider(
        value = rate,
        onValueChange = { rate = it },
        onValueChangeFinished = { settings.rate = rate; voice.applySettings() },
        valueRange = 0.6f..1.3f,
    )
    OutlinedButton(onClick = {
        voice.applySettings()
        voice.speak("Riley here. Loud and clear, ${settings.callName}. What's the job?")
    }) { Text("TEST VOICE") }
}

@Composable
private fun HandsFreeSection() {
    val context = LocalContext.current
    val status by WakeModel.status.collectAsState()
    LaunchedEffect(Unit) { WakeModel.refresh(context) }

    SectionTitle("HANDS-FREE")
    Text(
        "Say \"Riley\" and talk. The wake word is detected offline on the tablet; what you say after it is " +
            "transcribed by Groq Whisper (or Gemini if there's no Groq key). Say \"stop\" or \"that's all\" to end.",
        color = TextDim,
    )
    when (val s = status) {
        ModelStatus.Installed -> Text("Wake-word model: installed", color = TextDim)
        is ModelStatus.Downloading -> {
            Text("Downloading wake-word model… ${s.percent}%", color = TextDim)
            LinearProgressIndicator(progress = { s.percent / 100f }, modifier = Modifier.fillMaxWidth())
        }
        ModelStatus.NotInstalled, is ModelStatus.Failed -> {
            if (s is ModelStatus.Failed) Text("Download failed: ${s.message}", color = Danger)
            Text("Wake-word model not installed (about 36 MB, one-time download).", color = TextDim)
            Button(onClick = { WakeModel.download(context) }) { Text("DOWNLOAD WAKE-WORD MODEL") }
        }
    }

    val power = context.getSystemService(PowerManager::class.java)
    if (!power.isIgnoringBatteryOptimizations(context.packageName)) {
        Text("Battery optimisation is on for Riley. Turn it off so Android doesn't stop hands-free.", color = Danger)
        OutlinedButton(onClick = {
            context.startActivity(Intent(android.provider.Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }) { Text("OPEN BATTERY SETTINGS") }
    }
}

@Composable
private fun CalendarSection(settings: Settings) {
    val context = LocalContext.current
    var connected by remember { mutableStateOf(CalendarRepo.hasPermission(context)) }
    var calendars by remember { mutableStateOf(emptyList<CalendarInfo>()) }
    var selectedId by remember { mutableStateOf(settings.calendarId) }
    var alertsOn by remember { mutableStateOf(settings.meetingAlerts) }
    var lead by remember { mutableFloatStateOf(settings.meetingLeadMinutes.toFloat()) }

    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        connected = CalendarRepo.hasPermission(context)
        Alerts.rescheduleAll(context)
    }
    LaunchedEffect(connected) {
        if (connected) {
            calendars = withContext(Dispatchers.IO) { CalendarRepo.calendars(context) }
            if (selectedId < 0) selectedId = CalendarRepo.defaultCalendarId(context) ?: -1L
        }
    }

    SectionTitle("MEETINGS (CALENDAR)")
    if (!connected) {
        Text(
            "Riley uses the calendar of the Google account signed in on this tablet. To use your main account, add it " +
                "in Android Settings → Passwords & accounts, and turn on Calendar sync.",
            color = TextDim,
        )
        Button(onClick = {
            permissionLauncher.launch(arrayOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR))
        }) { Text("CONNECT CALENDAR") }
        return
    }

    Text("Calendar connected. New meetings go into:", color = TextDim)
    if (calendars.isEmpty()) Text("No writable calendars found on this tablet yet.", color = Danger)
    calendars.forEach { cal ->
        ChoiceRow("${cal.name}  (${cal.account})", selected = cal.id == selectedId) {
            selectedId = cal.id
            settings.calendarId = cal.id
        }
    }
    ToggleRow("Heads-up before meetings", alertsOn) {
        alertsOn = it
        settings.meetingAlerts = it
        Alerts.rescheduleAll(context)
    }
    if (alertsOn) {
        Text("Heads-up ${lead.toInt()} minutes before", color = TextDim)
        Slider(
            value = lead,
            onValueChange = { lead = it },
            onValueChangeFinished = {
                settings.meetingLeadMinutes = lead.toInt()
                Alerts.scheduleNextMeeting(context)
            },
            valueRange = 5f..30f,
            steps = 4,
        )
    }
}

@Composable
private fun WhatsAppSection() {
    val context = LocalContext.current
    val changes by Store.changes.collectAsState()
    var granted by remember { mutableStateOf(WhatsApp.notificationAccessGranted(context)) }
    var newContact by remember { mutableStateOf("") }
    var autoReply by remember { mutableStateOf(false) }
    val watched = remember(changes) { Store.get(context).contacts() }
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(lifecycleOwner) {
        // Re-check after the user comes back from Android's settings screen.
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            granted = WhatsApp.notificationAccessGranted(context)
        }
    }

    SectionTitle("WHATSAPP")
    Text(
        "Riley reads WhatsApp the way a smartwatch does: through notifications, on your normal account. It only " +
            "reads the chats you list here, and it can only answer a chat that messaged recently. It can't start a new chat.",
        color = TextDim,
    )
    if (!granted) {
        Text("Notification access is off, so no messages reach Riley.", color = Danger)
        Button(onClick = { WhatsApp.openNotificationAccessSettings(context) }) { Text("GIVE NOTIFICATION ACCESS") }
        return
    }
    Text("Notification access: on", color = TextDim)

    watched.forEach { rule ->
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(rule.pattern, color = TextMain)
                Text(
                    if (rule.mode == "auto") "Riley answers this chat by itself" else "Riley tells you about it",
                    color = TextDim,
                )
            }
            TextButton(onClick = { Store.get(context).removeContact(rule.id) }) { Text("REMOVE", color = TextDim) }
        }
    }
    if (watched.isEmpty()) Text("No chats listed yet, so Riley is reading nothing.", color = TextDim)

    Field("Contact or group name (or part of it)", newContact) { newContact = it }
    ToggleRow("Let Riley answer this contact by itself", autoReply) { autoReply = it }
    Button(
        onClick = {
            Store.get(context).addContact(newContact.trim(), if (autoReply) "auto" else "notify")
            newContact = ""
            autoReply = false
        },
        enabled = newContact.trim().length >= 2,
    ) { Text("ADD CHAT") }
}

@Composable
private fun BriefSection(settings: Settings) {
    val context = LocalContext.current
    var enabled by remember { mutableStateOf(settings.briefEnabled) }
    var minuteOfDay by remember { mutableStateOf(settings.briefMinuteOfDay) }

    fun changeTime(delta: Int) {
        minuteOfDay = (minuteOfDay + delta + 24 * 60) % (24 * 60)
        settings.briefMinuteOfDay = minuteOfDay
        Alerts.scheduleBrief(context)
    }

    SectionTitle("MORNING BRIEF")
    Text("Every day Riley sums up your meetings, what's due and what's overdue, and reads it out.", color = TextDim)
    ToggleRow("Morning brief", enabled) {
        enabled = it
        settings.briefEnabled = it
        Alerts.scheduleBrief(context)
    }
    if (enabled) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = { changeTime(-15) }) { Text("−15 MIN") }
            Spacer(Modifier.width(16.dp))
            Text("%02d:%02d".format(minuteOfDay / 60, minuteOfDay % 60), color = TextMain)
            Spacer(Modifier.width(16.dp))
            OutlinedButton(onClick = { changeTime(15) }) { Text("+15 MIN") }
        }
    }
    OutlinedButton(onClick = { Alerts.runBriefNow(context) }) { Text("BRIEF ME NOW") }
}

@Composable
private fun PhoneSection(settings: Settings) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val linkUp by LinkService.listening.collectAsState()
    var callsOn by remember { mutableStateOf(settings.callsEnabled) }
    var forMeetings by remember { mutableStateOf(settings.callForMeetings) }
    var forTasks by remember { mutableStateOf(settings.callForUrgentTasks) }
    var escalate by remember { mutableFloatStateOf(settings.escalateMinutes.toFloat()) }
    var testResult by remember { mutableStateOf<String?>(null) }
    val address = remember { Notifier.localAddress() ?: "not on Wi-Fi" }

    SectionTitle("CALLS TO MY PHONE")
    Text(
        "For things that can't slip, Riley rings your phone: it keeps ringing back every few minutes until you " +
            "answer, up to three times. Install \"Riley Phone\" on your Android phone and pair it with the three " +
            "lines below.",
        color = TextDim,
    )
    ToggleRow("Let Riley ring my phone", callsOn) {
        callsOn = it
        settings.callsEnabled = it
        if (it) LinkService.start(context) else LinkService.stop(context)
    }
    if (!callsOn) return

    ToggleRow("Ring before meetings", forMeetings) {
        forMeetings = it
        settings.callForMeetings = it
    }
    ToggleRow("Ring for urgent tasks", forTasks) {
        forTasks = it
        settings.callForUrgentTasks = it
    }
    Text("Ring back after ${escalate.toInt()} minutes with no answer", color = TextDim)
    Slider(
        value = escalate,
        onValueChange = { escalate = it },
        onValueChangeFinished = { settings.escalateMinutes = escalate.toInt() },
        valueRange = 2f..15f,
        steps = 12,
    )

    SectionTitle("PAIRING")
    Text("Channel: ${settings.phoneTopic}", color = TextMain)
    Text("Code: ${settings.linkToken}", color = TextMain)
    Text("This tablet: $address:${settings.linkPort}", color = TextMain)
    Text(
        if (linkUp) {
            "Phone link: on. Talking to Riley works while both are on this Wi-Fi."
        } else {
            "Phone link: off. Rings still work; talking back needs the link."
        },
        color = if (linkUp) TextDim else Danger,
    )
    Text(
        "The channel name is also its password, so keep it private. Only the one-line headline crosses the public " +
            "relay; what you say to Riley stays on your Wi-Fi.",
        color = TextDim,
    )
    OutlinedButton(onClick = {
        testResult = "Ringing…"
        scope.launch {
            val failure = Notifier.send(
                context,
                Notifier.KIND_CALL,
                "Test call from Riley",
                "Comms check, ${settings.callName}. This is a test.",
                "test",
            )
            testResult = failure?.let { "Failed: $it" } ?: "Sent. Your phone should ring."
        }
    }) { Text("TEST CALL MY PHONE") }
    testResult?.let { Text(it, color = if (it.startsWith("Failed")) Danger else TextDim) }
}

@Composable
private fun ToggleRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = TextMain, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

@Composable
private fun RemindersSection(settings: Settings) {
    val context = LocalContext.current
    var speakAlerts by remember { mutableStateOf(settings.speakAlerts) }
    SectionTitle("REMINDERS")
    ToggleRow("Say reminders, heads-ups and the brief out loud", speakAlerts) {
        speakAlerts = it
        settings.speakAlerts = it
    }
    val alarms = context.getSystemService(AlarmManager::class.java)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !alarms.canScheduleExactAlarms()) {
        Text("Exact reminders are off, so alerts can arrive a few minutes late.", color = Danger)
        OutlinedButton(onClick = {
            context.startActivity(
                Intent(android.provider.Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${context.packageName}")),
            )
        }) { Text("ALLOW EXACT REMINDERS") }
    } else {
        Text("Exact reminders: on", color = TextDim)
    }
}

@Composable
private fun ChoiceRow(label: String, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = null)
        Spacer(Modifier.width(8.dp))
        Text(label, color = TextMain)
    }
}
