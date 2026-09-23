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
    data class Error(val code: Int, val message: String) : RecognitionEvent
    data object EndOfSpeech : RecognitionEvent
}

/**
 * Thin wrapper around the platform [SpeechRecognizer]. Not every Android
 * build ships a fully on-device model for every language - Arabic on-device
 * ASR in particular is rare outside Samsung/Pixel, and budget devices often
 * have none at all - so this never assumes on-device works; the caller
 * ([StandaloneViewModel]) decides per attempt via [useOnDevice] and falls
 * back to the standard (usually network-backed, via whatever RecognitionService
 * the device ships, typically the Google app) recognizer when it doesn't.
 * Either recognizer talks only to the OS/Google, never to our own engine -
 * that's what keeps this "standalone" in the sense the user means: no
 * server, no Windows machine, no dependency on this repo's Python code.
 */
class OnDeviceSpeechRecognizer(private val context: Context) {

    private var recognizer: SpeechRecognizer? = null

    fun isOnDeviceAvailable(): Boolean = SpeechRecognizer.isOnDeviceRecognitionAvailable(context)

    /**
     * Listens for a single utterance in [languageTag] (BCP-47, e.g. "en-US",
     * "ar-JO") and emits partial results as they arrive, then exactly one
     * Final or Error, then completes. SpeechRecognizer is single-shot by
     * design, not a continuous stream - the caller re-invokes this per turn.
     */
    fun listen(languageTag: String, useOnDevice: Boolean): Flow<RecognitionEvent> = callbackFlow {
        val sr = if (useOnDevice) {
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
                trySend(RecognitionEvent.Error(error, describeError(error)))
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
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, useOnDevice)
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
        SpeechRecognizer.ERROR_CLIENT -> "No speech recognition service available on this device"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Missing microphone permission"
        SpeechRecognizer.ERROR_NETWORK -> "Network error"
        SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Network timeout"
        SpeechRecognizer.ERROR_NO_MATCH -> "No speech recognized"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Recognizer busy"
        SpeechRecognizer.ERROR_SERVER -> "Server error"
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech input"
        // Int literals, not the named API 33+ constants (ERROR_LANGUAGE_NOT_SUPPORTED /
        // ERROR_LANGUAGE_UNAVAILABLE) - this app's minSdk is 31, and referencing the
        // literal values keeps this correct on any compileSdk without an API guard.
        12 -> "No on-device language model installed for this language"
        13 -> "On-device speech recognition temporarily unavailable"
        else -> "Unknown recognition error ($error)"
    }
}
