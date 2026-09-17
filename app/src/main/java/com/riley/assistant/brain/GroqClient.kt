package com.riley.assistant.brain

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

/** Backup brain. Groq exposes an OpenAI-compatible chat completions API. */
class GroqClient(private val apiKey: String, private val model: String) : LlmClient {

    override suspend fun respond(
        system: String,
        history: List<Turn>,
        tools: List<ToolDef>,
        runTool: (name: String, args: JSONObject) -> String,
    ): String = withContext(Dispatchers.IO) {
        val url = "https://api.groq.com/openai/v1/chat/completions"
        val messages = JSONArray().put(JSONObject().put("role", "system").put("content", system))
        history.forEach { messages.put(JSONObject().put("role", it.role).put("content", it.text)) }
        val toolSpecs = JSONArray()
        tools.forEach { tool ->
            toolSpecs.put(
                JSONObject().put("type", "function").put(
                    "function",
                    JSONObject().put("name", tool.name).put("description", tool.description).put("parameters", tool.parameters),
                ),
            )
        }

        repeat(MAX_TOOL_ROUNDS) {
            ensureActive()
            val body = JSONObject()
                .put("model", model)
                .put("messages", messages)
                .put("temperature", 0.6)
            if (toolSpecs.length() > 0) body.put("tools", toolSpecs).put("tool_choice", "auto")
            val response = Http.postJson(url, body, mapOf("Authorization" to "Bearer $apiKey"))
            val message = response.optJSONArray("choices")?.optJSONObject(0)?.optJSONObject("message")
                ?: throw IOException("Groq sent no answer")
            val content = if (message.isNull("content")) "" else message.optString("content")
            val calls = message.optJSONArray("tool_calls")

            if (calls == null || calls.length() == 0) return@withContext content.trim()

            messages.put(JSONObject().put("role", "assistant").put("content", content).put("tool_calls", calls))
            for (i in 0 until calls.length()) {
                val call = calls.getJSONObject(i)
                val function = call.getJSONObject("function")
                val args = runCatching { JSONObject(function.optString("arguments", "{}")) }.getOrElse { JSONObject() }
                val result = runTool(function.getString("name"), args)
                messages.put(JSONObject().put("role", "tool").put("tool_call_id", call.getString("id")).put("content", result))
            }
        }
        throw IOException("Groq kept calling tools without answering")
    }
}
