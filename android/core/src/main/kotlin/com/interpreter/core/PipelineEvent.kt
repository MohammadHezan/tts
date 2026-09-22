package com.interpreter.core

import kotlinx.serialization.Serializable

/**
 * Mirrors engine/app/schema.py's PipelineEvent wire schema exactly - one per
 * JSON text WebSocket message from the engine. `audio`, when present, is
 * still base64-encoded PCM16 (the engine's `ser_json_bytes="base64"`) -
 * decode it with kotlin.io.encoding.Base64 right before playback.
 */
@Serializable
data class PipelineEvent(
    val type: EventType,
    val lang: String,
    val text: String = "",
    val turnId: String,
    val seq: Int = 0,
    val latencyMs: Double? = null,
    val isFinalSegment: Boolean = false,
    val audio: String? = null,
    val audioSampleRate: Int? = null,
    val timestamp: Double = 0.0,
    val error: String? = null,
) {
    companion object {
        fun fromJson(text: String): PipelineEvent = EngineJson.decodeFromString(serializer(), text)
    }
}
