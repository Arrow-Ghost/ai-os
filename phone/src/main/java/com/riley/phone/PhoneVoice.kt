package com.riley.phone

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/** Riley's voice on the phone: same low British delivery as the tablet. */
class PhoneVoice(context: Context) {
    private val waiters = ConcurrentHashMap<String, CompletableDeferred<Unit>>()

    @Volatile
    private var ready = false
    private var tts: TextToSpeech? = null

    init {
        tts = TextToSpeech(context.applicationContext) { status ->
            if (status != TextToSpeech.SUCCESS) return@TextToSpeech
            val engine = tts ?: return@TextToSpeech
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) = Unit

                override fun onDone(utteranceId: String?) = finish(utteranceId)

                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) = finish(utteranceId)

                override fun onError(utteranceId: String?, errorCode: Int) = finish(utteranceId)

                override fun onStop(utteranceId: String?, interrupted: Boolean) = finish(utteranceId)
            })
            val british = runCatching { engine.voices }.getOrNull().orEmpty()
                .filter { it.locale.language == "en" && it.locale.country == "GB" }
                .minByOrNull { if (it.isNetworkConnectionRequired) 1 else 0 }
            if (british != null) engine.setVoice(british) else engine.setLanguage(Locale.UK)
            engine.setPitch(0.85f)
            engine.setSpeechRate(0.98f)
            ready = true
        }
    }

    suspend fun say(text: String) {
        if (text.isBlank()) return
        withTimeoutOrNull(5_000) { while (!ready) delay(100) } ?: return
        val id = "riley-${System.nanoTime()}"
        val done = CompletableDeferred<Unit>()
        waiters[id] = done
        val queued = tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, id) == TextToSpeech.SUCCESS
        if (queued) withTimeoutOrNull(15_000L + text.length * 150L) { done.await() }
        waiters.remove(id)
    }

    fun shutdown() {
        ready = false
        runCatching {
            tts?.stop()
            tts?.shutdown()
        }
        tts = null
        waiters.values.forEach { it.complete(Unit) }
        waiters.clear()
    }

    private fun finish(utteranceId: String?) {
        utteranceId?.let { waiters.remove(it)?.complete(Unit) }
    }
}
