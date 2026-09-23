package com.interpreter.app.meetingbot

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.interpreter.core.ConversationState
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.WebSocket

data class MeetingBotUiState(
    val serverUrl: String = "http://192.168.1.50:8765",
    val meetingUrl: String = "",
    val botName: String = "AI Interpreter",
    val botId: String? = null,
    val botState: String? = null,
    val busy: Boolean = false,
    val error: String? = null,
    val conversation: ConversationState = ConversationState(),
)

/**
 * Remote control for a meeting bot running on the translator server: sends it
 * into a Zoom/Meet call, polls its state, and mirrors its live captions (what
 * it heard, what it said) through core's ConversationState reducer - the same
 * one Engine mode uses, since the server emits the same PipelineEvent schema.
 */
class MeetingBotViewModel : ViewModel() {

    private val http = OkHttpClient()
    private val _uiState = MutableStateFlow(MeetingBotUiState())
    val uiState: StateFlow<MeetingBotUiState> = _uiState.asStateFlow()

    private var events: WebSocket? = null
    private var pollJob: Job? = null

    fun setServerUrl(value: String) = _uiState.update { it.copy(serverUrl = value) }

    fun setMeetingUrl(value: String) = _uiState.update { it.copy(meetingUrl = value) }

    fun setBotName(value: String) = _uiState.update { it.copy(botName = value) }

    fun sendBot() {
        val state = _uiState.value
        if (state.meetingUrl.isBlank()) {
            _uiState.update { it.copy(error = "Paste a Zoom or Google Meet link first.") }
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(busy = true, error = null) }
            try {
                val api = MeetingBotApi(http, state.serverUrl)
                val bot = api.createBot(state.meetingUrl.trim(), state.botName.ifBlank { "AI Interpreter" })
                _uiState.update { it.copy(botId = bot.id, botState = bot.state, conversation = ConversationState()) }
                track(api, bot.id)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (error: Exception) {
                _uiState.update { it.copy(error = error.message ?: "Could not reach the translator server") }
            } finally {
                _uiState.update { it.copy(busy = false) }
            }
        }
    }

    fun removeBot() {
        val botId = _uiState.value.botId ?: return
        val api = MeetingBotApi(http, _uiState.value.serverUrl)
        viewModelScope.launch {
            try {
                api.leaveBot(botId)
                _uiState.update { it.copy(botState = "leaving") }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (error: Exception) {
                _uiState.update { it.copy(error = error.message) }
            }
        }
    }

    private fun track(api: MeetingBotApi, botId: String) {
        stopTracking()
        events = api.streamEvents(botId) { event ->
            _uiState.update { it.copy(conversation = it.conversation.reduce(event)) }
        }
        pollJob = viewModelScope.launch {
            while (isActive) {
                try {
                    val bot = api.getBot(botId)
                    _uiState.update { it.copy(botState = bot.state) }
                    if (bot.state in FINISHED_STATES) {
                        events?.close(1000, null)
                        _uiState.update { it.copy(botId = null) }
                        return@launch
                    }
                } catch (cancellation: CancellationException) {
                    throw cancellation
                } catch (_: Exception) {
                    // A missed poll is harmless - the next one retries.
                }
                delay(3000)
            }
        }
    }

    private fun stopTracking() {
        pollJob?.cancel()
        pollJob = null
        events?.close(1000, null)
        events = null
    }

    override fun onCleared() {
        stopTracking()
        super.onCleared()
    }

    private companion object {
        val FINISHED_STATES = setOf("ended", "fatal_error")
    }
}
