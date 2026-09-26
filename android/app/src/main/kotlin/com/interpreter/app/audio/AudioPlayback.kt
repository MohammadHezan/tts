package com.interpreter.app.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * Plays queued translated-sentence audio back-to-back on a dedicated thread,
 * so sentences never overlap or clip and playback never blocks mic capture.
 */
class AudioPlayback {
    private val queue = LinkedBlockingQueue<Pair<ByteArray, Int>>()
    private var playbackThread: Thread? = null

    @Volatile
    private var isRunning = false

    fun start() {
        if (isRunning) return
        isRunning = true
        playbackThread = thread(name = "AudioPlaybackThread") {
            while (isRunning) {
                val item = queue.poll(200, TimeUnit.MILLISECONDS) ?: continue
                runCatching { playBlocking(item.first, item.second) }
            }
        }
    }

    fun enqueue(pcm16: ByteArray, sampleRate: Int) {
        if (pcm16.isEmpty()) return
        queue.put(pcm16 to sampleRate)
    }

    private fun playBlocking(pcm16: ByteArray, sampleRate: Int) {
        val minBufferSize = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setBufferSizeInBytes(maxOf(minBufferSize, pcm16.size))
            .setTransferMode(AudioTrack.MODE_STATIC)
            .build()
        try {
            track.write(pcm16, 0, pcm16.size)
            track.play()
            val durationMs = (pcm16.size / 2) * 1000L / sampleRate // 2 bytes/sample
            Thread.sleep(durationMs)
        } finally {
            track.stop()
            track.release()
        }
    }

    fun stop() {
        isRunning = false
        playbackThread?.join(500)
        playbackThread = null
        queue.clear()
    }
}
