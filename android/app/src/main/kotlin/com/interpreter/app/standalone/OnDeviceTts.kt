package com.interpreter.app.standalone

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.Locale
import java.util.UUID
import kotlin.coroutines.resume

/**
 * Wraps the platform TextToSpeech engine, built into every Android device -
 * common languages (English/Arabic) need no extra download, unlike the
 * engine's Kokoro/Piper models this replaces for standalone mode.
 */
class OnDeviceTts(context: Context) {

    private val audioManager =
        context.applicationContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    private var ready = false
    private val pendingReady = mutableListOf<() -> Unit>()

    private val tts: TextToSpeech = TextToSpeech(context.applicationContext, ::onTtsInit)

    private fun onTtsInit(status: Int) {
        ready = status == TextToSpeech.SUCCESS
        if (ready) {
            tts.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
        }
        val callbacks = pendingReady.toList()
        pendingReady.clear()
        callbacks.forEach { it() }
    }

    private suspend fun awaitReady() {
        if (ready) return
        suspendCancellableCoroutine<Unit> { continuation ->
            pendingReady += { if (continuation.isActive) continuation.resume(Unit) }
        }
    }

    fun isLanguageAvailable(locale: Locale): Boolean = when (tts.isLanguageAvailable(locale)) {
        TextToSpeech.LANG_AVAILABLE, TextToSpeech.LANG_COUNTRY_AVAILABLE, TextToSpeech.LANG_COUNTRY_VAR_AVAILABLE -> true
        else -> false
    }

    /** Speaks [text] in [locale] and suspends until playback finishes. */
    suspend fun speak(text: String, locale: Locale) {
        awaitReady()

        // SpeechRecognizer (especially when it captures through a Bluetooth
        // headset's mic) can silently leave the audio session in
        // MODE_IN_COMMUNICATION with Bluetooth SCO engaged - that's the
        // earbuds' low-quality call-audio link, not the normal A2DP media
        // link TTS output needs. Left on, playback can go nowhere audible
        // (or fall back to the phone speaker) instead of the earbuds. Force
        // it back to normal media routing before every utterance so this
        // plays over A2DP like any other media audio.
        try {
            if (audioManager.isBluetoothScoOn) {
                audioManager.isBluetoothScoOn = false
                audioManager.stopBluetoothSco()
            }
            audioManager.mode = AudioManager.MODE_NORMAL
        } catch (_: SecurityException) {
            // BLUETOOTH_CONNECT wasn't granted - playback still works over
            // whatever routing is already active (e.g. the phone speaker).
        }

        tts.language = locale
        val utteranceId = UUID.randomUUID().toString()
        suspendCancellableCoroutine<Unit> { continuation ->
            tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) = Unit

                override fun onDone(utteranceId: String?) {
                    if (continuation.isActive) continuation.resume(Unit)
                }

                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) {
                    if (continuation.isActive) continuation.resume(Unit)
                }
            })
            tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId)
        }
    }

    fun shutdown() {
        tts.stop()
        tts.shutdown()
    }
}
