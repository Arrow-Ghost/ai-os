package com.riley.assistant.brain

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

class GeminiClient(private val apiKey: String, private val model: String) : LlmClient {

    override suspend fun respond(
        system: String,
        history: List<Turn>,
        tools: List<ToolDef>,
        runTool: (name: String, args: JSONObject) -> String,
    ): String = withContext(Dispatchers.IO) {
        val url = "https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent"
        val contents = JSONArray()
        history.forEach { turn ->
            contents.put(
                JSONObject()
                    .put("role", if (turn.role == "user") "user" else "model")
                    .put("parts", JSONArray().put(JSONObject().put("text", turn.text))),
            )
        }
        val declarations = JSONArray()
        tools.forEach { tool ->
            declarations.put(
                JSONObject().put("name", tool.name).put("description", tool.description).put("parameters", tool.parameters),
            )
        }

        repeat(MAX_TOOL_ROUNDS) {
            ensureActive()
            val body = JSONObject()
                .put("systemInstruction", JSONObject().put("parts", JSONArray().put(JSONObject().put("text", system))))
                .put("contents", contents)
            if (declarations.length() > 0) {
                body.put("tools", JSONArray().put(JSONObject().put("functionDeclarations", declarations)))
            }
            val response = Http.postJson(url, body, mapOf("x-goog-api-key" to apiKey))

            val content = response.optJSONArray("candidates")?.optJSONObject(0)?.optJSONObject("content")
                ?: throw IOException("Gemini sent no answer ${response.optJSONObject("promptFeedback") ?: ""}")
            val parts = content.optJSONArray("parts") ?: JSONArray()
            val calls = (0 until parts.length()).mapNotNull { parts.getJSONObject(it).optJSONObject("functionCall") }

            if (calls.isEmpty()) {
                return@withContext buildString {
                    for (i in 0 until parts.length()) {
                        val part = parts.getJSONObject(i)
                        if (!part.optBoolean("thought") && part.has("text")) append(part.getString("text"))
                    }
                }.trim()
            }

            // Send the model's turn back unchanged: Gemini needs its thought signatures to continue tool calls.
            contents.put(content)
            val results = JSONArray()
            calls.forEach { call ->
                val name = call.getString("name")
                val result = runTool(name, call.optJSONObject("args") ?: JSONObject())
                val functionResponse = JSONObject().put("name", name).put("response", JSONObject().put("result", result))
                if (call.has("id")) functionResponse.put("id", call.getString("id"))
                results.put(JSONObject().put("functionResponse", functionResponse))
            }
            contents.put(JSONObject().put("role", "user").put("parts", results))
        }
        throw IOException("Gemini kept calling tools without answering")
    }
}
