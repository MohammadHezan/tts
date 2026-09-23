package com.interpreter.app.captionbridge

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.interpreter.app.standalone.MlKitTranslator
import com.interpreter.app.standalone.OnDeviceTts
import com.interpreter.app.standalone.SpeakerLanguage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class CaptionLine(val original: String, val translated: String)

enum class CaptionBridgeStatus { IDLE, TRANSLATING, SPEAKING, ERROR }

data class CaptionBridgeUiState(
    val captionLanguage: SpeakerLanguage = SpeakerLanguage.ENGLISH,
    val rawDetectedText: String = "",
    val lines: List<CaptionLine> = emptyList(),
    val status: CaptionBridgeStatus = CaptionBridgeStatus.IDLE,
    val errorMessage: String? = null,
)

/**
 * Translates whatever text CaptionAccessibilityService detects in Zoom/Meet's
 * own Live Captions. Listening-only by design - this has no way to send
 * anything back into the call, see android/README.md's Caption Bridge
 * section for the full explanation of that boundary.
 *
 * Caption text arrives incrementally (accessibility events fire as each
 * caption line grows word by word) - this debounces so a line is only
 * translated once it's stopped changing for [STABLE_MS], rather than
 * re-translating every partial update.
 */
class CaptionBridgeViewModel(application: Application) : AndroidViewModel(application) {

    private val translator = MlKitTranslator()
    private val tts = OnDeviceTts(application)

    private val _uiState = MutableStateFlow(CaptionBridgeUiState())
    val uiState: StateFlow<CaptionBridgeUiState> = _uiState.asStateFlow()

    private var collectJob: Job? = null
    private var debounceJob: Job? = null
    private var lastHandled: String = ""

    fun setCaptionLanguage(language: SpeakerLanguage) {
        _uiState.value = _uiState.value.copy(captionLanguage = language)
    }

    fun start() {
        if (collectJob?.isActive == true) return
        collectJob = viewModelScope.launch {
            CaptionAccessibilityService.captionText.collect { raw ->
                _uiState.value = _uiState.value.copy(rawDetectedText = raw)
                debounceJob?.cancel()
                debounceJob = launch {
                    delay(STABLE_MS)
                    handleStableCaption(raw)
                }
            }
        }
    }

    fun stop() {
        collectJob?.cancel()
        collectJob = null
        debounceJob?.cancel()
        debounceJob = null
    }

    private suspend fun handleStableCaption(text: String) {
        val trimmed = text.trim()
        if (trimmed.isEmpty() || trimmed == lastHandled) return
        lastHandled = trimmed

        val source = _uiState.value.captionLanguage
        val target = source.other()
        _uiState.value = _uiState.value.copy(status = CaptionBridgeStatus.TRANSLATING, errorMessage = null)
        try {
            val translated = translator.translate(trimmed, source.mlKitCode, target.mlKitCode)
            _uiState.value = _uiState.value.copy(
                status = CaptionBridgeStatus.SPEAKING,
                lines = (_uiState.value.lines + CaptionLine(trimmed, translated)).takeLast(MAX_LINES),
            )
            tts.speak(translated, target.ttsLocale)
            _uiState.value = _uiState.value.copy(status = CaptionBridgeStatus.IDLE)
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (error: Exception) {
            _uiState.value = _uiState.value.copy(
                status = CaptionBridgeStatus.ERROR,
                errorMessage = error.message ?: "Translation failed",
            )
        }
    }

    override fun onCleared() {
        stop()
        translator.close()
        tts.shutdown()
        super.onCleared()
    }

    private companion object {
        const val STABLE_MS = 900L
        const val MAX_LINES = 50
    }
}
