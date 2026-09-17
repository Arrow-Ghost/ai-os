package com.riley.assistant.brain

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** role: "user" or "assistant". */
data class Turn(val role: String, val text: String)

interface LlmClient {
    /** Runs the full tool-calling loop and returns Riley's final spoken reply. */
    suspend fun respond(
        system: String,
        history: List<Turn>,
        tools: List<ToolDef>,
        runTool: (name: String, args: JSONObject) -> String,
    ): String
}

class LlmException(val code: Int, message: String) : IOException("HTTP $code: $message")

/** A file sent in a multipart upload. */
class FilePart(val field: String, val fileName: String, val mimeType: String, val bytes: ByteArray)

/** Blocking HTTP helpers. Call from Dispatchers.IO. */
internal object Http {
    fun postJson(url: String, body: JSONObject, headers: Map<String, String>): JSONObject {
        val connection = open(url, headers, "application/json")
        try {
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
            return readJson(connection)
        } finally {
            connection.disconnect()
        }
    }

    fun postMultipart(url: String, headers: Map<String, String>, fields: Map<String, String>, file: FilePart): JSONObject {
        val boundary = "riley${System.nanoTime()}"
        val connection = open(url, headers, "multipart/form-data; boundary=$boundary")
        try {
            connection.outputStream.buffered().use { out ->
                for ((name, value) in fields) {
                    out.write("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n".toByteArray())
                }
                out.write(
                    ("--$boundary\r\nContent-Disposition: form-data; name=\"${file.field}\"; filename=\"${file.fileName}\"\r\n" +
                        "Content-Type: ${file.mimeType}\r\n\r\n").toByteArray(),
                )
                out.write(file.bytes)
                out.write("\r\n--$boundary--\r\n".toByteArray())
            }
            return readJson(connection)
        } finally {
            connection.disconnect()
        }
    }

    private fun open(url: String, headers: Map<String, String>, contentType: String): HttpURLConnection =
        (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 15_000
            readTimeout = 60_000
            doOutput = true
            setRequestProperty("Content-Type", contentType)
            for ((key, value) in headers) setRequestProperty(key, value)
        }

    private fun readJson(connection: HttpURLConnection): JSONObject {
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
        if (code !in 200..299) throw LlmException(code, text.take(300))
        return JSONObject(text)
    }
}

internal const val MAX_TOOL_ROUNDS = 6
