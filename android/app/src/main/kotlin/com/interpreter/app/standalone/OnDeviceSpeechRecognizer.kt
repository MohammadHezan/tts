package com.interpreter.app.standalone

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow

/** One outcome emitted while listening for a single utterance. */
sealed interface RecognitionEvent {
    data class Partial(val text: String) : RecognitionEvent
    data class Final(val text: String) : RecognitionEvent
    data class Error(val message: String) : RecognitionEvent
    data object EndOfSpeech : RecognitionEvent
}

/**
 * Thin wrapper around the platform [SpeechRecognizer]. Prefers the fully
 * on-device recognizer (API 31+, guaranteed to work with no network at all)
 * and falls back to the standard system recognizer when no on-device model
 * is installed for this device/language. Either way this talks only to the
 * OS - never to our own engine - which is what makes standalone mode work
 * with no server.
 */
class OnDeviceSpeechRecognizer(private val context: Context) {

    private var recognizer: SpeechRecognizer? = null

    fun isOnDeviceAvailable(): Boolean = SpeechRecognizer.isOnDeviceRecognitionAvailable(context)

    /**
     * Listens for a single utterance in [languageTag] (BCP-47, e.g. "en-US",
     * "ar-JO") and emits partial results as they arrive, then exactly one
     * Final or Error, then completes. SpeechRecognizer is single-shot by
     * design, not a continuous stream - call this again for the next turn.
     */
    fun listen(languageTag: String): Flow<RecognitionEvent> = callbackFlow {
        val sr = if (isOnDeviceAvailable()) {
            SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        } else {
            SpeechRecognizer.createSpeechRecognizer(context)
        }
        recognizer = sr

        sr.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) = Unit
            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(buffer: ByteArray?) = Unit

            override fun onEndOfSpeech() {
                trySend(RecognitionEvent.EndOfSpeech)
            }

            override fun onError(error: Int) {
                trySend(RecognitionEvent.Error(describeError(error)))
                close()
            }

            override fun onResults(results: Bundle?) {
                val text = results
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                    .orEmpty()
                if (text.isNotBlank()) trySend(RecognitionEvent.Final(text))
                close()
            }

            override fun onPartialResults(partialResults: Bundle?) {
                val text = partialResults
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                    .orEmpty()
                if (text.isNotBlank()) trySend(RecognitionEvent.Partial(text))
            }

            override fun onEvent(eventType: Int, params: Bundle?) = Unit
        })

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, languageTag)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
        }
        sr.startListening(intent)

        awaitClose {
            sr.stopListening()
            sr.destroy()
            recognizer = null
        }
    }

    fun cancel() {
        recognizer?.cancel()
    }

    private fun describeError(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_AUDIO -> "Audio recording error"
        SpeechRecognizer.ERROR_CLIENT -> "Client side error"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Missing microphone permission"
        SpeechRecognizer.ERROR_NETWORK -> "Network error"
        SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Network timeout"
        SpeechRecognizer.ERROR_NO_MATCH -> "No speech recognized"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Recognizer busy"
        SpeechRecognizer.ERROR_SERVER -> "Server error"
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech input"
        else -> "Unknown recognition error ($error)"
    }
}
