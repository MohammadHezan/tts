package com.interpreter.app.meetingbot

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
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

enum class ServerStatus { SEARCHING, FOUND, NOT_FOUND, NO_WIFI }

data class MeetingBotUiState(
    val serverStatus: ServerStatus = ServerStatus.SEARCHING,
    val serverUrl: String = "",
    val meetingUrl: String = "",
    val botId: String? = null,
    val botState: String? = null,
    val botProblem: String? = null,
    val busy: Boolean = false,
    val error: String? = null,
    val conversation: ConversationState = ConversationState(),
)

/**
 * Remote control for a meeting bot running on the interpreter's computer: finds
 * that computer on the Wi-Fi by itself (ServerFinder, remembered afterwards),
 * sends the bot into a call, follows its state, and mirrors its live captions
 * (what it heard, what it said) through core's ConversationState reducer - the
 * same one Engine mode uses, since the server emits the same PipelineEvent schema.
 */
class MeetingBotViewModel(application: Application) : AndroidViewModel(application) {

    private val http = OkHttpClient()
    private val finder = ServerFinder(application)
    private val prefs = application.getSharedPreferences("meeting_bot", Context.MODE_PRIVATE)
    private val _uiState = MutableStateFlow(MeetingBotUiState(serverUrl = prefs.getString(KEY_SERVER, "").orEmpty()))
    val uiState: StateFlow<MeetingBotUiState> = _uiState.asStateFlow()

    private var events: WebSocket? = null
    private var pollJob: Job? = null
    private var findJob: Job? = null

    init {
        findServer()
    }

    /** Checks the remembered computer first, then scans the Wi-Fi. */
    fun findServer() {
        findJob?.cancel()
        findJob = viewModelScope.launch {
            _uiState.update { it.copy(serverStatus = ServerStatus.SEARCHING, error = null) }
            val remembered = _uiState.value.serverUrl
            if (remembered.isNotBlank() && finder.isInterpreter(remembered)) {
                _uiState.update { it.copy(serverStatus = ServerStatus.FOUND) }
                return@launch
            }
            when (val result = finder.find()) {
                is ServerFinder.Result.Found -> useServer(result.url)
                ServerFinder.Result.NotFound -> _uiState.update { it.copy(serverStatus = ServerStatus.NOT_FOUND) }
                ServerFinder.Result.NoWifi -> _uiState.update { it.copy(serverStatus = ServerStatus.NO_WIFI) }
            }
        }
    }

    /** A typed-in address, for networks where scanning can't find the computer. */
    fun setServerUrl(value: String) {
        val url = normalizeServerUrl(value)
        viewModelScope.launch {
            _uiState.update { it.copy(serverStatus = ServerStatus.SEARCHING, error = null) }
            if (finder.isInterpreter(url)) {
                useServer(url)
            } else {
                _uiState.update { it.copy(serverStatus = ServerStatus.NOT_FOUND, error = "No interpreter answered at $url.") }
            }
        }
    }

    private fun useServer(url: String) {
        prefs.edit().putString(KEY_SERVER, url).apply()
        _uiState.update { it.copy(serverUrl = url, serverStatus = ServerStatus.FOUND) }
    }

    fun setMeetingUrl(value: String) = _uiState.update { it.copy(meetingUrl = value) }

    fun sendBot() {
        val state = _uiState.value
        if (state.meetingUrl.isBlank()) {
            _uiState.update { it.copy(error = "Paste a meeting link first.") }
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(busy = true, error = null, botProblem = null) }
            try {
                val api = MeetingBotApi(http, state.serverUrl)
                val bot = api.createBot(state.meetingUrl.trim(), BOT_NAME)
                _uiState.update { it.copy(botId = bot.id, botState = bot.state, conversation = ConversationState()) }
                track(api, bot.id)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (error: Exception) {
                _uiState.update { it.copy(error = error.message ?: "Could not reach the interpreter's computer.") }
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
                    _uiState.update { it.copy(botState = bot.state, botProblem = bot.problem) }
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

    companion object {
        const val BOT_NAME = "AI Interpreter"

        /** "192.168.1.50" -> "http://192.168.1.50:8765"; full URLs are kept as typed. */
        fun normalizeServerUrl(value: String): String {
            var url = value.trim().trimEnd('/')
            if (url.isEmpty()) return url
            if ("://" !in url) url = "http://$url"
            val hostAndPort = url.substringAfter("://").substringBefore('/')
            return if (':' in hostAndPort) url else "$url:${ServerFinder.PORT}"
        }

        private const val KEY_SERVER = "server_url"
        private val FINISHED_STATES = setOf("ended", "fatal_error")
    }
}
