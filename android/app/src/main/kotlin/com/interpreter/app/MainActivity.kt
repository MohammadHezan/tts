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
import com.interpreter.app.ui.InterpreterScreen
import com.interpreter.app.ui.InterpreterTheme
import com.interpreter.app.ui.InterpreterViewModel

class MainActivity : ComponentActivity() {

    private val viewModel: InterpreterViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            InterpreterTheme {
                var permissionsGranted by remember { mutableStateOf(false) }
                val permissionLauncher = rememberLauncherForActivityResult(
                    ActivityResultContracts.RequestMultiplePermissions(),
                ) { results -> permissionsGranted = results.values.all { it } }

                LaunchedEffect(Unit) {
                    viewModel.bindService()
                    permissionLauncher.launch(requiredPermissions())
                }

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
                )
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
