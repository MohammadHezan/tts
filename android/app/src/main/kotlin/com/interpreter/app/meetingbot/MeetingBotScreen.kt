@file:OptIn(ExperimentalMaterial3Api::class)

package com.interpreter.app.meetingbot

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.interpreter.core.Turn

/**
 * Sends the interpreter bot into a Google Meet / Teams / Zoom call and shows its
 * live captions. The bot runs on the computer where the interpreter was started
 * (start.sh / "Start Interpreter.bat"), which this screen finds on the Wi-Fi by
 * itself - it's the remote control, see android/README.md "Meeting Bot".
 */
@Composable
fun MeetingBotScreen(
    uiState: MeetingBotUiState,
    onFindServer: () -> Unit,
    onServerUrlChange: (String) -> Unit,
    onMeetingUrlChange: (String) -> Unit,
    onSendBot: () -> Unit,
    onRemoveBot: () -> Unit,
    onSwitchMode: () -> Unit,
) {
    val botActive = uiState.botId != null
    val clipboard = LocalClipboardManager.current
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
            ComputerStatus(uiState, onFindServer, onServerUrlChange)
            Spacer(Modifier.height(16.dp))

            OutlinedTextField(
                value = uiState.meetingUrl,
                onValueChange = onMeetingUrlChange,
                label = { Text("Meeting link (Google Meet, Teams or Zoom)") },
                singleLine = true,
                enabled = !botActive,
                trailingIcon = {
                    if (!botActive) {
                        TextButton(onClick = { clipboard.getText()?.text?.let { onMeetingUrlChange(it.trim()) } }) {
                            Text("Paste")
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))

            if (botActive) {
                Text(
                    text = botStatusText(uiState.botState, uiState.botProblem),
                    fontSize = 16.sp,
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = onRemoveBot, modifier = Modifier.fillMaxWidth()) {
                    Text("Remove interpreter from meeting")
                }
            } else {
                Button(
                    onClick = onSendBot,
                    enabled = uiState.serverStatus == ServerStatus.FOUND && !uiState.busy &&
                        uiState.meetingUrl.isNotBlank(),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(56.dp),
                ) {
                    Text(if (uiState.busy) "Sending…" else "Send interpreter into meeting", fontSize = 17.sp)
                }
                // How the last bot ended, until the next one is sent.
                if (uiState.botState in setOf("ended", "fatal_error")) {
                    Spacer(Modifier.height(8.dp))
                    Text(botStatusText(uiState.botState, uiState.botProblem), fontSize = 14.sp)
                }
            }

            uiState.error?.let { message ->
                Spacer(Modifier.height(8.dp))
                Text(text = message, fontSize = 14.sp, color = MaterialTheme.colorScheme.error)
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
private fun ComputerStatus(
    uiState: MeetingBotUiState,
    onFindServer: () -> Unit,
    onServerUrlChange: (String) -> Unit,
) {
    when (uiState.serverStatus) {
        ServerStatus.SEARCHING -> Row(verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
            Spacer(Modifier.width(10.dp))
            Text("Looking for your computer on this Wi-Fi…", fontSize = 15.sp)
        }

        ServerStatus.FOUND -> Text(
            "✓ Connected to your computer",
            fontSize = 15.sp,
            color = MaterialTheme.colorScheme.primary,
        )

        ServerStatus.NO_WIFI -> Column {
            Text("Connect this phone to the same Wi-Fi as your computer.", fontSize = 15.sp)
            TextButton(onClick = onFindServer) { Text("Try again") }
        }

        ServerStatus.NOT_FOUND -> Column {
            Text(
                "Can't find your computer. Start the interpreter on it, and make sure both are on the same Wi-Fi.",
                fontSize = 15.sp,
            )
            TextButton(onClick = onFindServer) { Text("Try again") }
            var typed by remember { mutableStateOf("") }
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = typed,
                    onValueChange = { typed = it },
                    label = { Text("Or type its address, e.g. 192.168.1.50") },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { onServerUrlChange(typed) }, enabled = typed.isNotBlank()) { Text("Connect") }
            }
        }
    }
}

private fun botStatusText(state: String?, problem: String?): String = when (state) {
    null, "ready", "joining" -> "Joining… In the meeting, let \"${MeetingBotViewModel.BOT_NAME}\" in."
    "waiting_room" -> "Waiting to be let in - admit \"${MeetingBotViewModel.BOT_NAME}\" in the meeting."
    "joined_not_recording", "joined_recording", "joined_recording_paused", "joined_recording_permission_denied" ->
        "In the meeting and interpreting. Just talk."
    "leaving" -> "Leaving the meeting…"
    "ended" -> problem ?: "The interpreter has left the meeting."
    "fatal_error" -> problem ?: "The interpreter couldn't join the meeting."
    else -> state.orEmpty().replace('_', ' ')
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
