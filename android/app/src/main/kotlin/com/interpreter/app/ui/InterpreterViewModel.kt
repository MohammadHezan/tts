package com.interpreter.app.ui

import android.app.Application
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.interpreter.app.network.ConnectionStatus
import com.interpreter.app.service.InterpreterForegroundService
import com.interpreter.core.ConversationState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Binds to [InterpreterForegroundService] and mirrors its state into
 * lifecycle-safe StateFlows the UI can always collect, even across the
 * brief window where the service is unbound (e.g. process recreation).
 */
class InterpreterViewModel(application: Application) : AndroidViewModel(application) {

    private var service: InterpreterForegroundService? = null
    private var isBound = false

    private val _conversationState = MutableStateFlow(ConversationState())
    val conversationState: StateFlow<ConversationState> = _conversationState.asStateFlow()

    private val _connectionStatus = MutableStateFlow(ConnectionStatus.DISCONNECTED)
    val connectionStatus: StateFlow<ConnectionStatus> = _connectionStatus.asStateFlow()

    private val _engineUrl = MutableStateFlow("ws://192.168.1.42:8000/ws")
    val engineUrl: StateFlow<String> = _engineUrl.asStateFlow()

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val bound = (binder as InterpreterForegroundService.LocalBinder).getService()
            service = bound
            isBound = true
            viewModelScope.launch { bound.conversationState.collect { _conversationState.value = it } }
            viewModelScope.launch { bound.connectionStatus.collect { _connectionStatus.value = it } }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            isBound = false
        }
    }

    fun bindService() {
        val context = getApplication<Application>()
        val intent = Intent(context, InterpreterForegroundService::class.java)
        ContextCompat.startForegroundService(context, intent)
        context.bindService(intent, connection, Context.BIND_AUTO_CREATE)
    }

    fun unbindService() {
        if (isBound) {
            getApplication<Application>().unbindService(connection)
            isBound = false
        }
    }

    fun setEngineUrl(url: String) {
        _engineUrl.value = url
    }

    fun start() {
        service?.startInterpreting(_engineUrl.value)
    }

    fun stop() {
        service?.stopInterpreting()
    }

    override fun onCleared() {
        stop()
        unbindService()
        super.onCleared()
    }
}
