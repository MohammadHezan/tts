package com.interpreter.app

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.interpreter.app.meetingbot.MeetingBotScreen
import com.interpreter.app.meetingbot.MeetingBotViewModel
import com.interpreter.app.standalone.StandaloneScreen
import com.interpreter.app.standalone.StandaloneViewModel
import com.interpreter.app.ui.InterpreterScreen
import com.interpreter.app.ui.InterpreterTheme
import com.interpreter.app.ui.InterpreterViewModel

private enum class AppMode { STANDALONE, ENGINE, MEETING_BOT }

class MainActivity : ComponentActivity() {

    private val viewModel: InterpreterViewModel by viewModels()
    private val standaloneViewModel: StandaloneViewModel by viewModels()
    private val meetingBotViewModel: MeetingBotViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            InterpreterTheme {
                // Standalone (on-device ASR + ML Kit translate + platform TTS,
                // no server) is the default - see android/README.md for why.
                // Engine mode (connects to the Python engine over WebSocket)
                // stays available for higher-quality, glossary/context-aware
                // translation when a server is reachable on the LAN.
                var mode by remember { mutableStateOf(AppMode.STANDALONE) }

                var permissionsGranted by remember { mutableStateOf(false) }
                val permissionLauncher = rememberLauncherForActivityResult(
                    ActivityResultContracts.RequestMultiplePermissions(),
                ) { results -> permissionsGranted = results.values.all { it } }

                LaunchedEffect(Unit) {
                    permissionLauncher.launch(requiredPermissions())
                }

                // The engine service (and its persistent notification) only
                // has a reason to exist once the user opts into Engine mode -
                // standalone mode must start with no background service at all.
                LaunchedEffect(mode) {
                    if (mode == AppMode.ENGINE) viewModel.bindService()
                }

                when (mode) {
                    AppMode.STANDALONE -> {
                        val uiState by standaloneViewModel.uiState.collectAsState()
                        StandaloneScreen(
                            uiState = uiState,
                            onSpeakerLanguageChange = standaloneViewModel::setSpeakerLanguage,
                            onMicClick = {
                                if (permissionsGranted) {
                                    standaloneViewModel.toggleListening()
                                } else {
                                    permissionLauncher.launch(requiredPermissions())
                                }
                            },
                            onSwitchMode = {
                                standaloneViewModel.stopListening()
                                mode = AppMode.MEETING_BOT
                            },
                        )
                    }

                    AppMode.MEETING_BOT -> {
                        val uiState by meetingBotViewModel.uiState.collectAsState()
                        // Leaving this screen doesn't pull the bot out of the call -
                        // it keeps interpreting on the server until removed.
                        MeetingBotScreen(
                            uiState = uiState,
                            onFindServer = meetingBotViewModel::findServer,
                            onServerUrlChange = meetingBotViewModel::setServerUrl,
                            onMeetingUrlChange = meetingBotViewModel::setMeetingUrl,
                            onSendBot = meetingBotViewModel::sendBot,
                            onRemoveBot = meetingBotViewModel::removeBot,
                            onSwitchMode = { mode = AppMode.ENGINE },
                        )
                    }

                    AppMode.ENGINE -> {
                        val conversationState by viewModel.conversationState.collectAsState()
                        val connectionStatus by viewModel.connectionStatus.collectAsState()
                        val engineUrl by viewModel.engineUrl.collectAsState()

                        InterpreterScreen(
                            conversationState = conversationState,
                            connectionStatus = connectionStatus,
                            engineUrl = engineUrl,
                            onEngineUrlChange = viewModel::setEngineUrl,
                            onStartClick = {
                                if (permissionsGranted) viewModel.start() else permissionLauncher.launch(requiredPermissions())
                            },
                            onStopClick = viewModel::stop,
                            onSwitchMode = {
                                viewModel.stop()
                                mode = AppMode.STANDALONE
                            },
                        )
                    }
                }
            }
        }
    }

    private fun requiredPermissions(): Array<String> = buildList {
        add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            add(Manifest.permission.BLUETOOTH_CONNECT)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            add(Manifest.permission.POST_NOTIFICATIONS)
        }
    }.toTypedArray()
}
