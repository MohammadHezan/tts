@file:OptIn(ExperimentalMaterial3Api::class)

package com.interpreter.app.meetingbot

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.interpreter.core.Turn

/**
 * Sends the interpreter bot into a Zoom/Meet call and shows its live captions.
 * The bot itself runs on the translator server (docker compose / desktop app),
 * which is what can actually hear and speak in the call - this screen is the
 * remote control, see android/README.md "Meeting Bot".
 */
@Composable
fun MeetingBotScreen(
    uiState: MeetingBotUiState,
    onServerUrlChange: (String) -> Unit,
    onMeetingUrlChange: (String) -> Unit,
    onBotNameChange: (String) -> Unit,
    onSendBot: () -> Unit,
    onRemoveBot: () -> Unit,
    onSwitchMode: () -> Unit,
) {
    val botActive = uiState.botId != null
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Meeting Bot") },
                actions = { TextButton(onClick = onSwitchMode) { Text("Engine mode") } },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
        ) {
            Text(
                text = "Sends an AI interpreter into your Zoom or Google Meet call. It runs on your " +
                    "computer and joins as its own participant - it hears everyone and speaks the " +
                    "translation into the call. Use your computer's address on this Wi-Fi.",
                fontSize = 13.sp,
                color = MaterialTheme.colorScheme.secondary,
            )
            Spacer(Modifier.height(12.dp))

            OutlinedTextField(
                value = uiState.serverUrl,
                onValueChange = onServerUrlChange,
                label = { Text("Translator server") },
                singleLine = true,
                enabled = !botActive,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = uiState.meetingUrl,
                onValueChange = onMeetingUrlChange,
                label = { Text("Zoom or Google Meet link") },
                singleLine = true,
                enabled = !botActive,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = uiState.botName,
                onValueChange = onBotNameChange,
                label = { Text("Bot name in the meeting") },
                singleLine = true,
                enabled = !botActive,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))

            if (botActive) {
                Text(
                    text = "Bot: ${uiState.botState ?: "…"} - admit it from the waiting room if the meeting has one.",
                    fontSize = 14.sp,
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = onRemoveBot, modifier = Modifier.fillMaxWidth()) {
                    Text("Remove bot from meeting")
                }
            } else {
                Button(onClick = onSendBot, enabled = !uiState.busy, modifier = Modifier.fillMaxWidth()) {
                    Text(if (uiState.busy) "Sending…" else "Send interpreter into meeting", fontSize = 16.sp)
                }
            }

            uiState.error?.let { message ->
                Spacer(Modifier.height(8.dp))
                Text(text = message, fontSize = 13.sp, color = MaterialTheme.colorScheme.error)
            }

            Spacer(Modifier.height(12.dp))
            HorizontalDivider()

            LazyColumn(
                modifier = Modifier.fillMaxWidth(),
                contentPadding = PaddingValues(vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(uiState.conversation.turns.reversed()) { turn -> BotTurnCard(turn) }
            }
        }
    }
}

@Composable
private fun BotTurnCard(turn: Turn) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text("HEARD (${turn.sourceLang.uppercase()})", fontSize = 12.sp, color = MaterialTheme.colorScheme.secondary)
            Text(turn.sourceText, fontSize = 18.sp)
            if (turn.translations.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Text("BOT SAID (${turn.targetLang.uppercase()})", fontSize = 12.sp, color = MaterialTheme.colorScheme.primary)
                turn.translations.forEach { Text(it, fontSize = 20.sp) }
            }
        }
    }
}
