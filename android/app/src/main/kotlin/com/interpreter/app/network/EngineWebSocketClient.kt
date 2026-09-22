package com.interpreter.app.network

import com.interpreter.core.PipelineEvent
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString

/**
 * Thin wrapper around OkHttp's WebSocket client for the engine's `ws://host:port/ws`
 * endpoint: sends raw PCM16 mic frames as binary messages, parses incoming JSON
 * text messages into [PipelineEvent] (see engine/app/schema.py / core's PipelineEvent).
 */
class EngineWebSocketClient(
    private val client: OkHttpClient,
    private val url: String,
) {
    private var webSocket: WebSocket? = null

    fun connect(
        onEvent: (PipelineEvent) -> Unit,
        onStatusChange: (ConnectionStatus) -> Unit,
    ) {
        val request = Request.Builder().url(url).build()
        webSocket = client.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    onStatusChange(ConnectionStatus.CONNECTED)
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    runCatching { PipelineEvent.fromJson(text) }.onSuccess(onEvent)
                    // Malformed/unrecognized messages are dropped rather than crashing the
                    // session - the engine and app should always agree on the schema, but a
                    // stray log line or future field addition should never take down capture.
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    onStatusChange(ConnectionStatus.ERROR)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    onStatusChange(ConnectionStatus.DISCONNECTED)
                }
            },
        )
    }

    /** Sends one 16kHz mono PCM16 frame (see core's pcm16FrameSizeBytes) as a binary WS message. */
    fun sendFrame(pcm16: ByteArray) {
        webSocket?.send(ByteString.of(*pcm16))
    }

    fun close() {
        webSocket?.close(1000, "client closing")
        webSocket = null
    }
}
