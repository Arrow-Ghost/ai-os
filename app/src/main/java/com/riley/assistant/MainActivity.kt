package com.riley.assistant

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.speech.RecognizerIntent
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.riley.assistant.alerts.Alerts
import com.riley.assistant.brain.Conversation
import com.riley.assistant.brain.ToolExecutor
import com.riley.assistant.data.ChatMessage
import com.riley.assistant.data.Settings
import com.riley.assistant.data.Store
import com.riley.assistant.data.Task
import com.riley.assistant.killswitch.KillSwitch
import com.riley.assistant.listen.ListenService
import com.riley.assistant.listen.ListenState
import com.riley.assistant.listen.WakeModel
import com.riley.assistant.reminders.Reminders
import com.riley.assistant.ui.Bg
import com.riley.assistant.ui.ChatScreen
import com.riley.assistant.ui.DetonatedScreen
import com.riley.assistant.ui.RileyTheme
import com.riley.assistant.ui.Screen
import com.riley.assistant.ui.SettingsScreen
import com.riley.assistant.ui.TasksScreen
import com.riley.assistant.ui.TopBar
import com.riley.assistant.voice.RileyVoice
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private lateinit var settings: Settings
    private lateinit var voice: RileyVoice

    private val messages = mutableStateListOf<ChatMessage>()
    private val tasks = mutableStateListOf<Task>()
    private var screen by mutableStateOf(Screen.Chat)

    private val speechLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode != RESULT_OK) return@registerForActivityResult
        val heard = result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS).orEmpty()
        // Check every alternative the recogniser heard, so the kill switch never gets missed.
        val text = heard.firstOrNull { KillSwitch.matches(it) } ?: heard.firstOrNull()
        if (!text.isNullOrBlank()) handleInput(text)
    }

    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startHandsFree() else toast("Riley needs the microphone for hands-free.")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        settings = Settings(this)
        voice = RileyVoice.get(this)

        reload()
        Reminders.rescheduleAll(this)
        Alerts.rescheduleAll(this)
        WakeModel.refresh(this)
        askForNotifications()
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                Store.changes.collect { reload() }
            }
        }
        if (settings.handsFree && ListenService.state.value == ListenState.Off && !KillSwitch.detonated) startHandsFree()

        setContent {
            val detonated by KillSwitch.state.collectAsState()
            val busy by Conversation.busy.collectAsState()
            val listenState by ListenService.state.collectAsState()
            val listenError by ListenService.lastError.collectAsState()

            RileyTheme {
                if (detonated) {
                    DetonatedScreen()
                } else {
                    Column(Modifier.fillMaxSize().background(Bg).safeDrawingPadding()) {
                        TopBar(screen) { screen = it }
                        when (screen) {
                            Screen.Chat -> ChatScreen(
                                messages = messages,
                                busy = busy,
                                listenState = listenState,
                                listenError = listenError,
                                onSend = ::handleInput,
                                onMic = ::listen,
                                onToggleHandsFree = ::toggleHandsFree,
                                modifier = Modifier.weight(1f),
                            )
                            Screen.Tasks -> TasksScreen(
                                tasks,
                                onComplete = { runTaskTool("complete_task", it.id) },
                                onDelete = { runTaskTool("delete_task", it.id) },
                                modifier = Modifier.weight(1f),
                            )
                            Screen.Settings -> SettingsScreen(settings, voice, Modifier.weight(1f))
                        }
                    }
                }
            }
        }
    }

    private fun handleInput(raw: String) {
        val text = raw.trim()
        if (text.isEmpty() || KillSwitch.detonated) return
        if (!KillSwitch.matches(text) && Conversation.busy.value) {
            toast("Riley is still working on the last one.")
            return
        }
        lifecycleScope.launch {
            val reply = Conversation.handle(this@MainActivity, text) ?: return@launch
            voice.speak(reply)
        }
    }

    private fun listen() {
        // With hands-free on, the microphone already belongs to Riley: just skip the wake word.
        if (ListenService.state.value != ListenState.Off) {
            ListenService.listenNow(this)
            return
        }
        voice.stop()
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-IN")
            .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
            .putExtra(RecognizerIntent.EXTRA_PROMPT, "Talk to Riley")
        try {
            speechLauncher.launch(intent)
        } catch (e: ActivityNotFoundException) {
            toast("No speech recogniser on this tablet. Install or update the Google app.")
        }
    }

    private fun toggleHandsFree() {
        if (ListenService.state.value != ListenState.Off) {
            settings.handsFree = false
            ListenService.stop(this)
        } else {
            settings.handsFree = true
            startHandsFree()
        }
    }

    private fun startHandsFree() {
        when {
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED ->
                micPermission.launch(Manifest.permission.RECORD_AUDIO)
            settings.groqKey.isBlank() && settings.geminiKey.isBlank() -> {
                screen = Screen.Settings
                toast("Add your API keys first.")
            }
            !WakeModel.isInstalled(this) -> {
                screen = Screen.Settings
                toast("Download the wake-word model first (Settings → Hands-free).")
            }
            else -> ListenService.start(this)
        }
    }

    private fun runTaskTool(tool: String, taskId: Long) {
        lifecycleScope.launch {
            withContext(Dispatchers.IO) { ToolExecutor(applicationContext).run(tool, JSONObject().put("id", taskId)) }
        }
    }

    private fun reload() {
        if (KillSwitch.detonated) {
            messages.clear()
            tasks.clear()
            return
        }
        val store = Store.get(this)
        messages.clear()
        messages.addAll(store.chat())
        tasks.clear()
        tasks.addAll(store.tasks())
    }

    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun toast(text: String) {
        Toast.makeText(this, text, Toast.LENGTH_LONG).show()
    }
}
