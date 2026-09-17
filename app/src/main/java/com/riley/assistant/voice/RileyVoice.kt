package com.riley.assistant.voice

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
import android.util.Log
import com.riley.assistant.data.Settings
import com.riley.assistant.killswitch.KillSwitch
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/**
 * Riley's voice, shared by the chat screen and hands-free mode.
 *
 * It speaks through any Android text-to-speech engine installed on the tablet: the built-in Google one,
 * or a neural voice engine such as the free sherpa-onnx northern English male voice (see README).
 */
class RileyVoice private constructor(private val appContext: Context) {
    private val settings = Settings(appContext)
    private var tts: TextToSpeech? = null
    private var generation = 0
    private var pendingText: String? = null
    private val waiters = ConcurrentHashMap<String, CompletableDeferred<Unit>>()

    @Volatile
    var isReady = false
        private set

    init {
        connect()
    }

    fun engines(): List<TextToSpeech.EngineInfo> = runCatching { tts?.engines }.getOrNull().orEmpty()

    fun setEngine(packageName: String) {
        settings.ttsEngine = packageName
        settings.voiceName = ""
        connect()
    }

    fun englishVoices(): List<Voice> {
        if (!isReady) return emptyList()
        val all: Set<Voice> = runCatching { tts?.voices }.getOrNull().orEmpty()
        return all
            .filter { it.locale.language == "en" || it.locale.language == "eng" }
            .filterNot { it.features.orEmpty().contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED) }
            .sortedWith(compareBy<Voice>({ it.locale.country !in BRITISH }, { it.isNetworkConnectionRequired }, { it.name }))
    }

    fun currentVoiceName(): String = runCatching { tts?.voice?.name }.getOrNull().orEmpty()

    fun applySettings() {
        val engine = tts ?: return
        if (!isReady) return
        val voices = englishVoices()
        val chosen = voices.firstOrNull { it.name == settings.voiceName }
            ?: voices.firstOrNull { !it.isNetworkConnectionRequired }
            ?: voices.firstOrNull()
        val voiceSet = chosen != null && engine.setVoice(chosen) == TextToSpeech.SUCCESS
        if (!voiceSet && engine.setLanguage(Locale.UK) < TextToSpeech.LANG_AVAILABLE) engine.setLanguage(Locale.ENGLISH)
        engine.setPitch(settings.pitch)
        engine.setSpeechRate(settings.rate)
    }

    /** Speak and return immediately. */
    fun speak(text: String) {
        if (KillSwitch.detonated || text.isBlank()) return
        if (!isReady) {
            pendingText = text
            return
        }
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "riley-${System.nanoTime()}")
    }

    /** Speak and wait until Riley has finished talking. */
    suspend fun say(text: String) {
        if (KillSwitch.detonated || text.isBlank()) return
        withTimeoutOrNull(5_000) { while (!isReady) delay(100) } ?: return
        val id = "riley-${System.nanoTime()}"
        val done = CompletableDeferred<Unit>()
        waiters[id] = done
        val queued = withContext(Dispatchers.Main) {
            tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, id) == TextToSpeech.SUCCESS
        }
        if (queued) withTimeoutOrNull(15_000L + text.length * 150L) { done.await() }
        waiters.remove(id)
    }

    fun stop() {
        if (isReady) tts?.stop()
    }

    private fun connect() {
        isReady = false
        tts?.let { old -> runCatching { old.stop(); old.shutdown() } }
        val myGeneration = ++generation
        val requested = settings.ttsEngine.ifBlank { null }
        tts = TextToSpeech(appContext, { status -> onInit(status, myGeneration, requested) }, requested)
    }

    private fun onInit(status: Int, myGeneration: Int, requested: String?) {
        if (myGeneration != generation) return // replaced by a newer engine choice
        val engine = tts ?: return
        if (status != TextToSpeech.SUCCESS) {
            Log.w(TAG, "Voice engine ${requested ?: "default"} failed to start")
            if (requested != null) {
                settings.ttsEngine = ""
                connect()
            }
            return
        }
        engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {
                _speaking.value = true
            }

            override fun onDone(utteranceId: String?) = finished(utteranceId)

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) = finished(utteranceId)

            override fun onError(utteranceId: String?, errorCode: Int) = finished(utteranceId)

            override fun onStop(utteranceId: String?, interrupted: Boolean) = finished(utteranceId)
        })
        isReady = true
        applySettings()
        pendingText?.let {
            pendingText = null
            speak(it)
        }
    }

    private fun finished(utteranceId: String?) {
        utteranceId?.let { waiters.remove(it)?.complete(Unit) }
        _speaking.value = false
    }

    private fun shutdown() {
        isReady = false
        generation++
        runCatching {
            tts?.stop()
            tts?.shutdown()
        }
        tts = null
        waiters.values.forEach { it.complete(Unit) }
        waiters.clear()
        _speaking.value = false
    }

    companion object {
        private const val TAG = "RileyVoice"
        private val BRITISH = setOf("GB", "GBR")

        private val _speaking = MutableStateFlow(false)

        /** True while Riley is talking, so hands-free mode doesn't listen to its own voice. */
        val speaking: StateFlow<Boolean> = _speaking.asStateFlow()

        @Volatile private var instance: RileyVoice? = null

        /** First call must be on the main thread. */
        fun get(context: Context): RileyVoice =
            instance ?: synchronized(this) {
                instance ?: RileyVoice(context.applicationContext).also { instance = it }
            }

        fun release() {
            synchronized(this) {
                instance?.shutdown()
                instance = null
            }
        }
    }
}
