package com.interpreter.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.interpreter.app.R
import com.interpreter.app.network.ConnectionStatus
import com.interpreter.core.ConversationState
import com.interpreter.core.Turn

/**
 * Single-screen Interpreter UI (Phase 3 scope): connection settings, a big
 * live caption for in-progress speech, and a scrollback of completed turns
 * (source text + its translation(s)). Foldable-specific layouts (split view,
 * Flex mode, cover screen) are a later phase - this must work on a normal
 * phone screen first.
 */
@OptIn(ExperimentalMaterial3Api::class) // TopAppBar
@Composable
fun InterpreterScreen(
    conversationState: ConversationState,
    connectionStatus: ConnectionStatus,
    engineUrl: String,
    onEngineUrlChange: (String) -> Unit,
    onStartClick: () -> Unit,
    onStopClick: () -> Unit,
    onSwitchMode: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.app_name)) },
                actions = { TextButton(onClick = onSwitchMode) { Text("Standalone mode") } },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
        ) {
            ConnectionRow(connectionStatus)
            Spacer(Modifier.height(12.dp))

            OutlinedTextField(
                value = engineUrl,
                onValueChange = onEngineUrlChange,
                label = { Text(stringResource(R.string.engine_url_label)) },
                singleLine = true,
                enabled = connectionStatus == ConnectionStatus.DISCONNECTED,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))

            val isActive = connectionStatus == ConnectionStatus.CONNECTED ||
                connectionStatus == ConnectionStatus.CONNECTING
            Button(
                onClick = if (isActive) onStopClick else onStartClick,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    stringResource(if (isActive) R.string.stop_button else R.string.start_button),
                    fontSize = 18.sp,
                )
            }
            Spacer(Modifier.height(16.dp))

            if (conversationState.partialText.isNotBlank()) {
                PartialCaption(conversationState.partialText, conversationState.partialLang)
                Spacer(Modifier.height(16.dp))
            }

            HorizontalDivider()

            LazyColumn(
                modifier = Modifier.fillMaxWidth(),
                contentPadding = PaddingValues(vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(conversationState.turns.reversed()) { turn ->
                    TurnCard(turn)
                }
            }
        }
    }
}

@Composable
private fun ConnectionRow(status: ConnectionStatus) {
    val (label, color) = when (status) {
        ConnectionStatus.DISCONNECTED -> stringResource(R.string.status_disconnected) to MaterialTheme.colorScheme.onSurface
        ConnectionStatus.CONNECTING -> stringResource(R.string.status_connecting) to Color(0xFFE8A33D)
        ConnectionStatus.CONNECTED -> stringResource(R.string.status_connected) to Color(0xFF4CAF50)
        ConnectionStatus.ERROR -> stringResource(R.string.status_error) to MaterialTheme.colorScheme.error
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(text = label, color = color, fontSize = 16.sp)
    }
}

@Composable
private fun PartialCaption(text: String, lang: String) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
        Column(Modifier.padding(16.dp)) {
            Text(text = lang.uppercase(), fontSize = 12.sp, color = MaterialTheme.colorScheme.secondary)
            Text(
                text = text,
                fontSize = 26.sp,
                textAlign = TextAlign.Start,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun TurnCard(turn: Turn) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text(text = turn.sourceLang.uppercase(), fontSize = 12.sp, color = MaterialTheme.colorScheme.secondary)
            Text(text = turn.sourceText, fontSize = 20.sp)
            if (turn.translations.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                HorizontalDivider()
                Spacer(Modifier.height(8.dp))
                Text(text = turn.targetLang.uppercase(), fontSize = 12.sp, color = MaterialTheme.colorScheme.secondary)
                turn.translations.forEach { sentence ->
                    Text(text = sentence, fontSize = 22.sp)
                }
            }
        }
    }
}
