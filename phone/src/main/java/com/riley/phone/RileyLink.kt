package com.riley.phone

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** A ring from the tablet. */
data class RileyCall(
    val kind: String,
    val headline: String,
    val spoken: String,
    val itemId: String,
    val host: String,
    val port: Int,
)

/** Talks to Riley on the tablet over the home Wi-Fi. */
object RileyLink {
    const val KIND_CALL = "call"
    const val KIND_ALERT = "alert"
    const val KIND_DETONATE = "detonate"

    /** Reads a ring and checks it really came from the paired tablet. Null if it doesn't add up. */
    fun parse(settings: PhoneSettings, message: String): RileyCall? {
        val json = runCatching { JSONObject(message) }.getOrNull() ?: return null
        val kind = json.optString("kind")
        val headline = json.optString("headline")
        val itemId = json.optString("itemId")
        val expected = signature(settings.code, kind, itemId, headline)
        if (json.optString("sig") != expected) return null
        return RileyCall(
            kind = kind,
            headline = headline,
            spoken = json.optString("spoken"),
            itemId = itemId,
            host = json.optString("host"),
            port = json.optInt("port", 8787),
        )
    }

    fun signature(code: String, kind: String, itemId: String, headline: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest("$code|$kind|$itemId|$headline".toByteArray())
        return digest.joinToString("") { "%02x".format(it) }.take(32)
    }

    suspend fun ping(context: Context): String = withContext(Dispatchers.IO) {
        val answer = request(context, "GET", "/ping", null)
        "Connected to Riley (" + answer.optString("owner", "owner") + ")"
    }

    /** Sends what the owner said and returns Riley's reply. */
    suspend fun say(context: Context, text: String): String = withContext(Dispatchers.IO) {
        request(context, "POST", "/say", JSONObject().put("text", text)).optString("reply")
    }

    /** "done" or "snooze". */
    suspend fun acknowledge(context: Context, action: String, minutes: Int = 10): String = withContext(Dispatchers.IO) {
        request(context, "POST", "/ack", JSONObject().put("action", action).put("minutes", minutes))
            .optString("note", "Copy that.")
    }

    private fun request(context: Context, method: String, path: String, body: JSONObject?): JSONObject {
        val settings = PhoneSettings(context)
        val address = settings.tablet.ifBlank { throw IOException("no tablet address yet — pair first") }
        val connection = URL("http://$address$path").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 5_000
            connection.readTimeout = 60_000
            connection.setRequestProperty("X-Riley-Token", settings.code)
            connection.setRequestProperty("Content-Type", "application/json")
            if (body != null) {
                connection.doOutput = true
                connection.outputStream.use { it.write(body.toString().toByteArray()) }
            }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code == 401) throw IOException("the tablet rejected the pairing code")
            if (code !in 200..299) throw IOException("the tablet said $code")
            return JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }
}
