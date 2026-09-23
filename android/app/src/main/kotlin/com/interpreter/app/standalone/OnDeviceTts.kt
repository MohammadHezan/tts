package com.interpreter.app.standalone

import android.content.Context
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

    private var ready = false
    private val pendingReady = mutableListOf<() -> Unit>()

    private val tts: TextToSpeech = TextToSpeech(context.applicationContext) { status ->
        ready = status == TextToSpeech.SUCCESS
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
