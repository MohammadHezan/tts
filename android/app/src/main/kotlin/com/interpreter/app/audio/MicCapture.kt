package com.interpreter.app.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import com.interpreter.core.pcm16FrameSizeBytes
import kotlin.concurrent.thread

/**
 * Captures 16kHz mono PCM16 audio in fixed-size frames matching config.yaml's
 * audio.frame_ms (30ms by default), on a dedicated thread since AudioRecord.read()
 * blocks. Uses VOICE_COMMUNICATION as the audio source so the OS applies its
 * own echo cancellation/noise suppression - appropriate for a live conversation
 * app rather than raw MIC capture.
 */
class MicCapture(
    private val sampleRateHz: Int = 16000,
    private val frameBytes: Int = pcm16FrameSizeBytes(sampleRateHz = 16000, frameMs = 30),
) {
    private var audioRecord: AudioRecord? = null
    private var captureThread: Thread? = null

    @Volatile
    private var isRunning = false

    val isActive: Boolean get() = isRunning

    /** Caller must have verified RECORD_AUDIO is granted before calling this. */
    @SuppressLint("MissingPermission")
    fun start(onFrame: (ByteArray) -> Unit) {
        if (isRunning) return

        val minBufferSize = AudioRecord.getMinBufferSize(
            sampleRateHz,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = maxOf(minBufferSize, frameBytes * 4)

        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            sampleRateHz,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize,
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            throw IllegalStateException("AudioRecord failed to initialize (sampleRate=$sampleRateHz)")
        }

        audioRecord = record
        isRunning = true
        record.startRecording()

        captureThread = thread(name = "MicCaptureThread") {
            val buffer = ByteArray(frameBytes)
            while (isRunning) {
                val read = record.read(buffer, 0, frameBytes)
                if (read == frameBytes) {
                    onFrame(buffer.copyOf())
                }
            }
        }
    }

    fun stop() {
        isRunning = false
        captureThread?.join(500)
        captureThread = null
        audioRecord?.let {
            it.stop()
            it.release()
        }
        audioRecord = null
    }
}
