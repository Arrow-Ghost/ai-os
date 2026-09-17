package com.riley.assistant.link

import android.content.Context
import android.util.Log
import com.riley.assistant.brain.Conversation
import com.riley.assistant.data.Settings
import com.riley.assistant.killswitch.KillSwitch
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import java.io.BufferedReader
import java.io.Closeable
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket

/**
 * A very small HTTP server so the phone can talk to Riley over the home Wi-Fi.
 * Every request must carry the tablet's link token, and it only ever listens on the local network.
 */
class LinkServer(private val context: Context) : Closeable {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var socket: ServerSocket? = null

    fun start(port: Int) {
        val server = ServerSocket(port)
        socket = server
        scope.launch {
            while (!server.isClosed && !KillSwitch.detonated) {
                val client = try {
                    server.accept()
                } catch (e: Exception) {
                    break
                }
                scope.launch { serve(client) }
            }
        }
    }

    override fun close() {
        runCatching { socket?.close() }
        socket = null
        scope.cancel()
    }

    private fun serve(client: Socket) {
        client.use { connection ->
            connection.soTimeout = 15_000
            try {
                val reader = BufferedReader(InputStreamReader(connection.getInputStream()))
                val requestLine = reader.readLine() ?: return
                val parts = requestLine.split(' ')
                if (parts.size < 2) return
                val method = parts[0]
                val path = parts[1].substringBefore('?')

                var length = 0
                var token = ""
                while (true) {
                    val line = reader.readLine().orEmpty()
                    if (line.isEmpty()) break
                    val name = line.substringBefore(':').trim().lowercase()
                    val value = line.substringAfter(':').trim()
                    when (name) {
                        "content-length" -> length = value.toIntOrNull() ?: 0
                        "x-riley-token" -> token = value
                    }
                }

                if (!tokenMatches(token)) {
                    respond(connection.getOutputStream(), 401, JSONObject().put("error", "bad token"))
                    return
                }

                val body = if (length > 0) {
                    val buffer = CharArray(length.coerceAtMost(64 * 1024))
                    val read = reader.read(buffer)
                    JSONObject(String(buffer, 0, read.coerceAtLeast(0)))
                } else {
                    JSONObject()
                }
                handle(method, path, body, connection.getOutputStream())
            } catch (e: Exception) {
                Log.w(TAG, "link request failed", e)
            }
        }
    }

    private fun handle(method: String, path: String, body: JSONObject, out: OutputStream) {
        when {
            method == "GET" && path == "/ping" -> respond(
                out,
                200,
                JSONObject().put("ok", true).put("owner", Settings(context).callName),
            )

            method == "POST" && path == "/say" -> {
                val text = body.optString("text").trim()
                if (text.isEmpty()) {
                    respond(out, 400, JSONObject().put("error", "no text"))
                    return
                }
                // Same path as typing in the tablet's chat: the owner is speaking, from their paired phone.
                val reply = runBlocking { Conversation.handle(context, text) }
                respond(out, 200, JSONObject().put("reply", reply ?: "Busy with the last one. Try again."))
            }

            method == "POST" && path == "/ack" -> {
                val action = body.optString("action", "done")
                val minutes = body.optInt("minutes", 10)
                val note = Escalation.acknowledge(context, action, minutes)
                respond(out, 200, JSONObject().put("ok", true).put("note", note))
            }

            method == "GET" && path == "/pending" -> respond(
                out,
                200,
                JSONObject().put("pending", Settings(context).pendingCall),
            )

            else -> respond(out, 404, JSONObject().put("error", "no such endpoint"))
        }
    }

    private fun tokenMatches(given: String): Boolean {
        val expected = Settings(context).linkToken
        if (given.length != expected.length) return false
        var diff = 0
        for (i in expected.indices) diff = diff or (given[i].code xor expected[i].code)
        return diff == 0
    }

    private fun respond(out: OutputStream, code: Int, body: JSONObject) {
        val payload = body.toString().toByteArray()
        val head = "HTTP/1.1 $code ${if (code == 200) "OK" else "ERROR"}\r\n" +
            "Content-Type: application/json\r\n" +
            "Content-Length: ${payload.size}\r\n" +
            "Connection: close\r\n\r\n"
        out.write(head.toByteArray())
        out.write(payload)
        out.flush()
    }

    private companion object {
        const val TAG = "RileyLink"
    }
}
