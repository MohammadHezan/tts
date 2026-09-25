package com.interpreter.app.meetingbot

import com.interpreter.core.PipelineEvent
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

data class BotInfo(val id: String, val state: String, val problem: String? = null, val muted: Boolean = false)

class MeetingBotException(message: String) : Exception(message)

/**
 * Client for the translator server's /api/bots endpoints (engine/app/server.py).
 * The phone never talks to Attendee directly - the server holds its API key and
 * the bot itself runs on the server, which is the only place audio can be
 * captured from and fed into a Zoom/Meet call (see android/README.md).
 */
class MeetingBotApi(private val client: OkHttpClient, serverUrl: String) {

    private val baseUrl = serverUrl.trim().trimEnd('/').let { if ("://" in it) it else "http://$it" }

    suspend fun createBot(meetingUrl: String, botName: String): BotInfo {
        val body = buildJsonObject {
            put("meeting_url", meetingUrl)
            put("bot_name", botName)
        }
        return request("POST", "/api/bots", body.toString()).toBotInfo()
    }

    suspend fun getBot(botId: String): BotInfo = request("GET", "/api/bots/$botId", null).toBotInfo()

    /** Muted, the bot stays in the meeting but stops speaking; its captions keep coming. */
    suspend fun setMuted(botId: String, muted: Boolean): Boolean {
        val body = buildJsonObject { put("muted", muted) }
        val result = request("POST", "/api/bots/$botId/mute", body.toString())
        return (result["muted"] as? JsonPrimitive)?.booleanOrNull ?: muted
    }

    suspend fun leaveBot(botId: String) {
        request("POST", "/api/bots/$botId/leave", "")
    }

    /** Live caption events (what the bot heard / said). OkHttp maps http(s) to ws(s) itself. */
    fun streamEvents(botId: String, onEvent: (PipelineEvent) -> Unit): WebSocket {
        val request = Request.Builder().url("$baseUrl/api/bots/$botId/events").build()
        return client.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    runCatching { PipelineEvent.fromJson(text) }.onSuccess(onEvent)
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = Unit
            },
        )
    }

    private suspend fun request(method: String, path: String, jsonBody: String?): JsonObject =
        withContext(Dispatchers.IO) {
            val body = jsonBody?.toRequestBody(JSON_MEDIA_TYPE)
            val request = Request.Builder().url(baseUrl + path).method(method, body).build()
            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    throw MeetingBotException(errorDetail(text) ?: "Server returned HTTP ${response.code}")
                }
                if (text.isBlank()) JsonObject(emptyMap()) else Json.parseToJsonElement(text).jsonObject
            }
        }

    private fun errorDetail(body: String): String? =
        runCatching { Json.parseToJsonElement(body).jsonObject["detail"]?.jsonPrimitive?.content }.getOrNull()

    private fun JsonObject.toBotInfo(): BotInfo = BotInfo(
        id = this["id"]?.jsonPrimitive?.content ?: throw MeetingBotException("Server response had no bot id"),
        state = this["state"]?.jsonPrimitive?.content ?: "unknown",
        // Why it couldn't join or had to leave, already phrased for people (server.py _bot_problem).
        problem = (this["problem"] as? JsonPrimitive)?.takeIf { it.isString }?.content,
        muted = (this["muted"] as? JsonPrimitive)?.booleanOrNull ?: false,
    )

    private companion object {
        val JSON_MEDIA_TYPE = "application/json".toMediaType()
    }
}
