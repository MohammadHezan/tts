package com.interpreter.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioManager
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.util.Base64
import androidx.core.app.NotificationCompat
import com.interpreter.app.R
import com.interpreter.app.audio.AudioPlayback
import com.interpreter.app.audio.BluetoothAudioRouting
import com.interpreter.app.audio.MicCapture
import com.interpreter.app.network.ConnectionStatus
import com.interpreter.app.network.EngineWebSocketClient
import com.interpreter.core.ConversationState
import com.interpreter.core.EventType
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import okhttp3.OkHttpClient

/**
 * Owns the whole listen -> transcribe -> translate -> speak session: mic
 * capture, the engine WebSocket connection, and translated-audio playback.
 * Runs as a foreground service so a long conversation survives the app being
 * backgrounded (switching apps mid-sentence must not drop the connection).
 *
 * MainActivity both starts (startForegroundService) and binds to this
 * service: starting keeps it alive independent of the UI's lifecycle,
 * binding lets the UI observe [conversationState]/[connectionStatus].
 */
class InterpreterForegroundService : Service() {

    inner class LocalBinder : Binder() {
        fun getService(): InterpreterForegroundService = this@InterpreterForegroundService
    }

    private val binder = LocalBinder()

    private val _conversationState = MutableStateFlow(ConversationState())
    val conversationState: StateFlow<ConversationState> = _conversationState.asStateFlow()

    private val _connectionStatus = MutableStateFlow(ConnectionStatus.DISCONNECTED)
    val connectionStatus: StateFlow<ConnectionStatus> = _connectionStatus.asStateFlow()

    private var wsClient: EngineWebSocketClient? = null
    private val micCapture = MicCapture()
    private val audioPlayback = AudioPlayback()
    private lateinit var audioManager: AudioManager

    override fun onCreate() {
        super.onCreate()
        audioManager = getSystemService(AUDIO_SERVICE) as AudioManager
        createNotificationChannel()
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notification = buildNotification(getString(R.string.notification_idle))
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        return START_STICKY
    }

    /** Caller (MainActivity) must have verified RECORD_AUDIO + BLUETOOTH_CONNECT first. */
    fun startInterpreting(engineUrl: String) {
        if (wsClient != null) return
        _connectionStatus.value = ConnectionStatus.CONNECTING
        _conversationState.value = ConversationState()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
            BluetoothAudioRouting.preferLeAudioDevice(audioManager)
        }

        audioPlayback.start()

        val client = EngineWebSocketClient(OkHttpClient(), engineUrl)
        wsClient = client
        client.connect(
            onEvent = { event ->
                _conversationState.value = _conversationState.value.reduce(event)
                if (event.type == EventType.AUDIO && !event.audio.isNullOrEmpty()) {
                    val pcm16 = Base64.decode(event.audio, Base64.DEFAULT)
                    audioPlayback.enqueue(pcm16, event.audioSampleRate ?: 16000)
                }
                updateNotification(getString(R.string.notification_listening))
            },
            onStatusChange = { status -> _connectionStatus.value = status },
        )

        micCapture.start { frame -> wsClient?.sendFrame(frame) }
    }

    fun stopInterpreting() {
        micCapture.stop()
        wsClient?.close()
        wsClient = null
        audioPlayback.stop()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            BluetoothAudioRouting.clearPreferredDevice(audioManager)
            audioManager.mode = AudioManager.MODE_NORMAL
        }
        _connectionStatus.value = ConnectionStatus.DISCONNECTED
        updateNotification(getString(R.string.notification_idle))
    }

    override fun onDestroy() {
        stopInterpreting()
        super.onDestroy()
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(text: String): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .build()

    private fun updateNotification(text: String) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, buildNotification(text))
    }

    companion object {
        private const val NOTIFICATION_ID = 1
        private const val CHANNEL_ID = "interpreter_service"
    }
}
