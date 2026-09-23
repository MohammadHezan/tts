package com.interpreter.app.standalone

import android.app.Application
import android.speech.SpeechRecognizer
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.util.Locale
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** Which side of the conversation is about to speak. */
enum class SpeakerLanguage(
    val bcp47: String,
    val mlKitCode: String,
    val ttsLocale: Locale,
    val displayName: String,
) {
    ENGLISH("en-US", MlKitTranslator.ENGLISH, Locale.US, "English"),
    ARABIC("ar-JO", MlKitTranslator.ARABIC, Locale("ar", "JO"), "العربية"),
    ;

    fun other(): SpeakerLanguage = if (this == ENGLISH) ARABIC else ENGLISH
}

data class StandaloneTurn(
    val sourceLang: SpeakerLanguage,
    val sourceText: String,
    val targetLang: SpeakerLanguage,
    val translatedText: String,
)

enum class StandaloneStatus { IDLE, LISTENING, TRANSLATING, SPEAKING, ERROR }

data class StandaloneUiState(
    val speakerLanguage: SpeakerLanguage = SpeakerLanguage.ENGLISH,
    val status: StandaloneStatus = StandaloneStatus.IDLE,
    val partialText: String = "",
    val turns: List<StandaloneTurn> = emptyList(),
    val errorMessage: String? = null,
    val onDeviceAsrAvailable: Boolean = true,
)

/** Result of one listen-transcribe attempt, deciding what the session loop does next. */
private sealed interface TurnOutcome {
    data object Ok : TurnOutcome
    data class Retryable(val message: String) : TurnOutcome
    data class Fatal(val message: String) : TurnOutcome
}

/**
 * Fully standalone speak -> transcribe -> translate -> speak loop built only
 * from on-device Android platform APIs (SpeechRecognizer + ML Kit Translate +
 * TextToSpeech). No WebSocket, no server, no Python engine.
 *
 * Tapping the mic starts a continuous session: each finished utterance is
 * transcribed, translated, and spoken, then it immediately starts listening
 * for the next one - one tap for the whole conversation, not one tap per
 * sentence. Because not every Android build has an on-device model for
 * every language (budget/OEM devices especially, and Arabic on-device ASR
 * is rare outside Samsung/Pixel even on higher-end phones), each turn tries
 * the on-device recognizer first and transparently falls back to the
 * standard recognizer (whatever RecognitionService the device ships -
 * typically the Google app, usually network-backed) when the on-device
 * attempt fails before producing any speech at all.
 */
class StandaloneViewModel(application: Application) : AndroidViewModel(application) {

    private val recognizer = OnDeviceSpeechRecognizer(application)
    private val translator = MlKitTranslator()
    private val tts = OnDeviceTts(application)

    private val _uiState = MutableStateFlow(
        StandaloneUiState(onDeviceAsrAvailable = recognizer.isOnDeviceAvailable()),
    )
    val uiState: StateFlow<StandaloneUiState> = _uiState.asStateFlow()

    @Volatile private var sessionActive = false
    private var sessionJob: Job? = null

    fun setSpeakerLanguage(language: SpeakerLanguage) {
        // Read fresh each turn in the session loop below, so switching this
        // mid-conversation takes effect on the next utterance with no need
        // to stop/restart the session.
        _uiState.value = _uiState.value.copy(speakerLanguage = language)
    }

    fun toggleListening() {
        if (sessionActive) stopListening() else startListening()
    }

    private fun startListening() {
        if (sessionActive) return
        sessionActive = true
        _uiState.value = _uiState.value.copy(status = StandaloneStatus.LISTENING, partialText = "", errorMessage = null)

        sessionJob = viewModelScope.launch {
            var consecutiveFailures = 0
            try {
                while (sessionActive) {
                    if (_uiState.value.status != StandaloneStatus.LISTENING) {
                        _uiState.value = _uiState.value.copy(
                            status = StandaloneStatus.LISTENING,
                            partialText = "",
                            errorMessage = null,
                        )
                    }

                    val speaker = _uiState.value.speakerLanguage
                    val target = speaker.other()

                    when (val outcome = runTurn(speaker, target)) {
                        TurnOutcome.Ok -> consecutiveFailures = 0

                        is TurnOutcome.Retryable -> {
                            consecutiveFailures++
                            if (consecutiveFailures >= 4) {
                                sessionActive = false
                                _uiState.value = _uiState.value.copy(
                                    status = StandaloneStatus.ERROR,
                                    errorMessage = outcome.message,
                                )
                            } else {
                                delay(300)
                            }
                        }

                        is TurnOutcome.Fatal -> {
                            sessionActive = false
                            _uiState.value = _uiState.value.copy(
                                status = StandaloneStatus.ERROR,
                                errorMessage = outcome.message,
                            )
                        }
                    }
                }
            } finally {
                sessionActive = false
                if (_uiState.value.status == StandaloneStatus.LISTENING) {
                    _uiState.value = _uiState.value.copy(status = StandaloneStatus.IDLE)
                }
            }
        }
    }

    fun stopListening() {
        sessionActive = false
        recognizer.cancel()
        sessionJob?.cancel()
        sessionJob = null
        _uiState.value = _uiState.value.copy(status = StandaloneStatus.IDLE, partialText = "")
    }

    /** One listening turn, with an automatic on-device -> standard-recognizer fallback. */
    private suspend fun runTurn(speaker: SpeakerLanguage, target: SpeakerLanguage): TurnOutcome {
        val preferOnDevice = recognizer.isOnDeviceAvailable()
        val first = collectTurn(speaker, target, useOnDevice = preferOnDevice)
        if (!preferOnDevice || first !is TurnOutcome.Retryable) return first
        return collectTurn(speaker, target, useOnDevice = false)
    }

    private suspend fun collectTurn(speaker: SpeakerLanguage, target: SpeakerLanguage, useOnDevice: Boolean): TurnOutcome {
        var receivedSpeech = false
        var outcome: TurnOutcome = TurnOutcome.Ok
        recognizer.listen(speaker.bcp47, useOnDevice).collect { event ->
            when (event) {
                is RecognitionEvent.Partial -> {
                    receivedSpeech = true
                    _uiState.value = _uiState.value.copy(partialText = event.text)
                }

                is RecognitionEvent.Final -> {
                    receivedSpeech = true
                    handleFinalText(event.text, speaker, target)
                }

                is RecognitionEvent.Error -> {
                    outcome = if (event.code == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) {
                        TurnOutcome.Fatal(event.message)
                    } else {
                        TurnOutcome.Retryable(event.message)
                    }
                }

                RecognitionEvent.EndOfSpeech -> Unit
            }
        }
        // Some speech was actually heard even though the utterance didn't
        // finalize (e.g. a trailing NO_MATCH) - treat that as a normal empty
        // turn, not a failure worth falling back or counting toward the
        // consecutive-failure limit.
        return if (receivedSpeech && outcome is TurnOutcome.Retryable) TurnOutcome.Ok else outcome
    }

    private suspend fun handleFinalText(text: String, speaker: SpeakerLanguage, target: SpeakerLanguage) {
        _uiState.value = _uiState.value.copy(status = StandaloneStatus.TRANSLATING, partialText = "")
        try {
            val translated = translator.translate(text, speaker.mlKitCode, target.mlKitCode)
            val turn = StandaloneTurn(
                sourceLang = speaker,
                sourceText = text,
                targetLang = target,
                translatedText = translated,
            )
            _uiState.value = _uiState.value.copy(
                status = StandaloneStatus.SPEAKING,
                turns = _uiState.value.turns + turn,
            )
            tts.speak(translated, target.ttsLocale)
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (error: Exception) {
            _uiState.value = _uiState.value.copy(errorMessage = error.message ?: "Translation failed")
        }
    }

    override fun onCleared() {
        stopListening()
        translator.close()
        tts.shutdown()
        super.onCleared()
    }
}
