package com.interpreter.core

/**
 * Slices a raw PCM16 byte buffer into fixed-size frames (mirrors
 * engine/app/audio_utils.py's iter_frames), zero-padding the final partial
 * frame so every chunk sent to the engine is exactly `frameBytes` long.
 */
fun framePcm16(pcm16: ByteArray, frameBytes: Int): List<ByteArray> {
    require(frameBytes > 0) { "frameBytes must be positive" }
    val frames = mutableListOf<ByteArray>()
    var offset = 0
    while (offset < pcm16.size) {
        val end = minOf(offset + frameBytes, pcm16.size)
        val chunk = pcm16.copyOfRange(offset, end)
        frames += if (chunk.size < frameBytes) chunk.copyOf(frameBytes) else chunk
        offset += frameBytes
    }
    return frames
}

/** PCM16 mono frame size in bytes for `frameMs` at `sampleRateHz` (matches config.yaml's audio:). */
fun pcm16FrameSizeBytes(sampleRateHz: Int, frameMs: Int): Int =
    (sampleRateHz * frameMs / 1000) * 2 // 2 bytes/sample for 16-bit PCM
