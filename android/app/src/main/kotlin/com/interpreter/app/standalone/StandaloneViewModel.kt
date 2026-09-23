package com.interpreter.app.standalone

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.util.Locale
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
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

/**
 * Fully standalone speak -> transcribe -> translate -> speak loop built only
 * from on-device Android platform APIs (SpeechRecognizer + ML Kit Translate +
 * TextToSpeech). No WebSocket, no server, no Python engine - this is what
 * makes the app usable with nothing but the phone itself, the same way
 * Samsung's own Interpreter mode works standalone on-device.
 */
class StandaloneViewModel(application: Application) : AndroidViewModel(application) {

    private val recognizer = OnDeviceSpeechRecognizer(application)
    private val translator = MlKitTranslator()
    private val tts = OnDeviceTts(application)

    private val _uiState = MutableStateFlow(
        StandaloneUiState(onDeviceAsrAvailable = recognizer.isOnDeviceAvailable()),
    )
    val uiState: StateFlow<StandaloneUiState> = _uiState.asStateFlow()

    private var listenJob: Job? = null

    fun setSpeakerLanguage(language: SpeakerLanguage) {
        if (_uiState.value.status != StandaloneStatus.IDLE) return
        _uiState.value = _uiState.value.copy(speakerLanguage = language)
    }

    fun toggleListening() {
        if (listenJob?.isActive == true) stopListening() else startListening()
    }

    private fun startListening() {
        if (listenJob?.isActive == true) return
        val speaker = _uiState.value.speakerLanguage
        val target = speaker.other()

        listenJob = viewModelScope.launch {
            _uiState.value = _uiState.value.copy(
                status = StandaloneStatus.LISTENING,
                partialText = "",
                errorMessage = null,
            )
            try {
                recognizer.listen(speaker.bcp47).collect { event ->
                    when (event) {
                        is RecognitionEvent.Partial ->
                            _uiState.value = _uiState.value.copy(partialText = event.text)

                        is RecognitionEvent.Final ->
                            handleFinalText(event.text, speaker, target)

                        is RecognitionEvent.Error ->
                            _uiState.value = _uiState.value.copy(
                                status = StandaloneStatus.ERROR,
                                partialText = "",
                                errorMessage = event.message,
                            )

                        RecognitionEvent.EndOfSpeech -> Unit
                    }
                }
            } finally {
                if (_uiState.value.status == StandaloneStatus.LISTENING) {
                    _uiState.value = _uiState.value.copy(status = StandaloneStatus.IDLE)
                }
            }
        }
    }

    fun stopListening() {
        recognizer.cancel()
        listenJob?.cancel()
        listenJob = null
        _uiState.value = _uiState.value.copy(status = StandaloneStatus.IDLE, partialText = "")
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
            _uiState.value = _uiState.value.copy(status = StandaloneStatus.IDLE)
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (error: Exception) {
            _uiState.value = _uiState.value.copy(
                status = StandaloneStatus.ERROR,
                errorMessage = error.message ?: "Translation failed",
            )
        }
    }

    override fun onCleared() {
        stopListening()
        translator.close()
        tts.shutdown()
        super.onCleared()
    }
}
