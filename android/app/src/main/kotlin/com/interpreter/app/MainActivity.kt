package com.interpreter.app

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import com.interpreter.app.captionbridge.CaptionAccessibilityService
import com.interpreter.app.captionbridge.CaptionBridgeScreen
import com.interpreter.app.captionbridge.CaptionBridgeViewModel
import com.interpreter.app.standalone.StandaloneScreen
import com.interpreter.app.standalone.StandaloneViewModel
import com.interpreter.app.ui.InterpreterScreen
import com.interpreter.app.ui.InterpreterTheme
import com.interpreter.app.ui.InterpreterViewModel

private enum class AppMode { STANDALONE, ENGINE, CAPTION_BRIDGE }

class MainActivity : ComponentActivity() {

    private val viewModel: InterpreterViewModel by viewModels()
    private val standaloneViewModel: StandaloneViewModel by viewModels()
    private val captionBridgeViewModel: CaptionBridgeViewModel by viewModels()

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
                                mode = AppMode.CAPTION_BRIDGE
                            },
                        )
                    }

                    AppMode.CAPTION_BRIDGE -> {
                        val uiState by captionBridgeViewModel.uiState.collectAsState()
                        var accessibilityEnabled by remember { mutableStateOf(false) }
                        val lifecycleOwner = LocalLifecycleOwner.current

                        // Enabling the service happens in system Settings, outside this
                        // screen - re-check on every resume so coming back from there
                        // (or from just switching apps and back) reflects reality.
                        DisposableEffect(lifecycleOwner) {
                            val observer = LifecycleEventObserver { _, event ->
                                if (event == Lifecycle.Event.ON_RESUME) {
                                    accessibilityEnabled = CaptionAccessibilityService.isEnabled(this@MainActivity)
                                }
                            }
                            lifecycleOwner.lifecycle.addObserver(observer)
                            onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
                        }

                        LaunchedEffect(mode) {
                            if (mode == AppMode.CAPTION_BRIDGE) {
                                accessibilityEnabled = CaptionAccessibilityService.isEnabled(this@MainActivity)
                                captionBridgeViewModel.start()
                            }
                        }

                        CaptionBridgeScreen(
                            uiState = uiState,
                            accessibilityServiceEnabled = accessibilityEnabled,
                            onCaptionLanguageChange = captionBridgeViewModel::setCaptionLanguage,
                            onOpenAccessibilitySettings = {
                                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                            },
                            onSwitchMode = {
                                captionBridgeViewModel.stop()
                                mode = AppMode.ENGINE
                            },
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
