package com.riley.assistant.listen

import android.content.Context
import android.util.Base64
import com.riley.assistant.brain.FilePart
import com.riley.assistant.brain.Http
import com.riley.assistant.data.Settings
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale

/** Turns recorded speech into text: Groq Whisper first (fast, good with Hinglish), Gemini as backup. */
object Transcriber {
    private const val GROQ_MODEL = "whisper-large-v3-turbo"

    // Whisper sometimes "hears" these in noise.
    private val PHANTOM = setOf("", "you", "thanks for watching", "thank you for watching", "subtitles by the amaraorg community")

    suspend fun transcribe(context: Context, utterance: Utterance): String = withContext(Dispatchers.IO) {
        val settings = Settings(context)
        val wav = wav(utterance.samples, Mic.SAMPLE_RATE)
        var text: String? = null
        var failure: Exception? = null

        if (settings.groqKey.isNotBlank()) {
            try {
                text = groq(settings.groqKey, wav)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                failure = e
            }
        }
        if (text == null && settings.geminiKey.isNotBlank()) {
            try {
                text = gemini(settings.geminiKey, settings.geminiModel, wav)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                failure = e
            }
        }
        text ?: throw (failure ?: IOException("Add a Groq or Gemini key in Settings so Riley can hear you"))
    }

    fun isPhantom(text: String): Boolean =
        text.lowercase(Locale.ROOT).filter { it.isLetter() || it == ' ' }.trim() in PHANTOM

    private fun groq(apiKey: String, wav: ByteArray): String =
        Http.postMultipart(
            url = "https://api.groq.com/openai/v1/audio/transcriptions",
            headers = mapOf("Authorization" to "Bearer $apiKey"),
            fields = mapOf("model" to GROQ_MODEL, "response_format" to "json", "temperature" to "0"),
            file = FilePart("file", "speech.wav", "audio/wav", wav),
        ).optString("text").trim()

    private fun gemini(apiKey: String, model: String, wav: ByteArray): String {
        val parts = JSONArray()
            .put(
                JSONObject().put(
                    "text",
                    "Transcribe this voice command word for word. The speaker is Indian and may mix Hindi and English; " +
                        "write Hindi words in Latin script. Reply with only the transcript, or nothing if there is no speech.",
                ),
            )
            .put(JSONObject().put("inlineData", JSONObject().put("mimeType", "audio/wav").put("data", Base64.encodeToString(wav, Base64.NO_WRAP))))
        val body = JSONObject().put("contents", JSONArray().put(JSONObject().put("role", "user").put("parts", parts)))
        val response = Http.postJson(
            "https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent",
            body,
            mapOf("x-goog-api-key" to apiKey),
        )
        val answer = response.optJSONArray("candidates")?.optJSONObject(0)?.optJSONObject("content")?.optJSONArray("parts")
            ?: return ""
        return buildString {
            for (i in 0 until answer.length()) {
                val part = answer.getJSONObject(i)
                if (!part.optBoolean("thought") && part.has("text")) append(part.getString("text"))
            }
        }.trim()
    }

    private fun wav(samples: ShortArray, sampleRate: Int): ByteArray {
        val dataSize = samples.size * 2
        val buffer = ByteBuffer.allocate(44 + dataSize).order(ByteOrder.LITTLE_ENDIAN)
        buffer.put("RIFF".toByteArray()).putInt(36 + dataSize).put("WAVE".toByteArray())
        buffer.put("fmt ".toByteArray()).putInt(16)
            .putShort(1) // PCM
            .putShort(1) // mono
            .putInt(sampleRate)
            .putInt(sampleRate * 2) // byte rate
            .putShort(2) // block align
            .putShort(16) // bits per sample
        buffer.put("data".toByteArray()).putInt(dataSize)
        for (sample in samples) buffer.putShort(sample)
        return buffer.array()
    }
}
