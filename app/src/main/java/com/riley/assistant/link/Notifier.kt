package com.riley.assistant.link

import android.content.Context
import com.riley.assistant.brain.Http
import com.riley.assistant.data.Settings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.Inet4Address
import java.net.NetworkInterface
import java.security.MessageDigest

/**
 * Sends a ring to the owner's phone through a push relay (ntfy by default).
 *
 * What crosses the relay is one short line. The relay is public, so the headline is all it ever sees:
 * the conversation itself runs straight between phone and tablet over the home Wi-Fi.
 */
object Notifier {
    const val KIND_CALL = "call"
    const val KIND_ALERT = "alert"
    const val KIND_DETONATE = "detonate"

    /** Returns null on success, or a reason it failed. */
    suspend fun send(
        context: Context,
        kind: String,
        headline: String,
        spoken: String,
        itemId: String = "",
    ): String? {
        val settings = Settings(context)
        return sendRaw(context, settings, kind, headline, spoken, itemId, signature(settings.linkToken, kind, itemId, headline))
    }

    /** Used directly by the kill switch, which must send before its own settings are wiped. */
    suspend fun sendRaw(
        context: Context,
        settings: Settings,
        kind: String,
        headline: String,
        spoken: String,
        itemId: String,
        signature: String,
    ): String? = withContext(Dispatchers.IO) {
        val payload = JSONObject()
            .put("kind", kind)
            .put("headline", headline)
            .put("spoken", spoken)
            .put("itemId", itemId)
            .put("host", localAddress().orEmpty())
            .put("port", settings.linkPort)
            // Proves the ring came from this tablet without ever putting the token on the relay.
            .put("sig", signature)
        val body = JSONObject()
            .put("topic", settings.phoneTopic)
            .put("title", if (kind == KIND_CALL) "Riley is calling" else "Riley")
            .put("message", payload.toString())
            .put("priority", if (kind == KIND_CALL) 5 else 4)
            .put("tags", JSONArray().put(if (kind == KIND_CALL) "phone" else "bell"))
        try {
            Http.postJson(settings.pushServer, body, emptyMap())
            null
        } catch (e: Exception) {
            e.message ?: "could not reach the push relay"
        }
    }

    fun signature(token: String, kind: String, itemId: String, headline: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest("$token|$kind|$itemId|$headline".toByteArray())
        return digest.joinToString("") { "%02x".format(it) }.take(32)
    }

    /** This tablet's address on the home network, for the phone to talk back to. */
    fun localAddress(): String? = runCatching {
        NetworkInterface.getNetworkInterfaces().toList()
            .filter { it.isUp && !it.isLoopback }
            .flatMap { it.inetAddresses.toList() }
            .filterIsInstance<Inet4Address>()
            .firstOrNull { !it.isLoopbackAddress && it.isSiteLocalAddress }
            ?.hostAddress
    }.getOrNull()
}
