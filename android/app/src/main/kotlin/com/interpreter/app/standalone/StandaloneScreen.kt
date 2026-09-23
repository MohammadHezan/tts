@file:OptIn(ExperimentalMaterial3Api::class)

package com.interpreter.app.standalone

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Standalone, server-free conversation screen: pick who's about to speak,
 * tap the mic, talk - on-device ASR transcribes, ML Kit translates, the
 * platform TTS speaks the translation back. Nothing here touches the
 * network except a one-time ML Kit model download.
 */
@Composable
fun StandaloneScreen(
    uiState: StandaloneUiState,
    onSpeakerLanguageChange: (SpeakerLanguage) -> Unit,
    onMicClick: () -> Unit,
    onSwitchMode: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Interpreter") },
                actions = { TextButton(onClick = onSwitchMode) { Text("Engine mode") } },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            if (!uiState.onDeviceAsrAvailable) {
                Text(
                    text = "On-device speech model isn't installed for this phone - " +
                        "recognition may need a data connection the first time you speak.",
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.secondary,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
            }

            LanguageToggleRow(uiState.speakerLanguage, uiState.status, onSpeakerLanguageChange)
            Spacer(Modifier.height(20.dp))

            MicButton(status = uiState.status, onClick = onMicClick)
            Spacer(Modifier.height(12.dp))
            Text(text = statusLabel(uiState.status), fontSize = 14.sp, color = MaterialTheme.colorScheme.secondary)

            uiState.errorMessage?.let { message ->
                Spacer(Modifier.height(8.dp))
                Text(
                    text = message,
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.error,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth(),
                )
            }

            if (uiState.partialText.isNotBlank()) {
                Spacer(Modifier.height(16.dp))
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
                    Text(
                        text = uiState.partialText,
                        fontSize = 22.sp,
                        textAlign = TextAlign.Start,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(16.dp),
                    )
                }
            }

            Spacer(Modifier.height(16.dp))
            HorizontalDivider()

            LazyColumn(
                modifier = Modifier.fillMaxWidth(),
                contentPadding = PaddingValues(vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(uiState.turns.reversed()) { turn ->
                    StandaloneTurnCard(turn)
                }
            }
        }
    }
}

@Composable
private fun LanguageToggleRow(
    selected: SpeakerLanguage,
    status: StandaloneStatus,
    onChange: (SpeakerLanguage) -> Unit,
) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        SpeakerLanguage.entries.forEach { language ->
            FilterChip(
                selected = selected == language,
                enabled = status == StandaloneStatus.IDLE,
                onClick = { onChange(language) },
                label = { Text("I speak ${language.displayName}") },
            )
        }
    }
}

@Composable
private fun MicButton(status: StandaloneStatus, onClick: () -> Unit) {
    val (background, label) = when (status) {
        StandaloneStatus.IDLE -> MaterialTheme.colorScheme.primary to "🎙" // microphone
        StandaloneStatus.LISTENING -> Color(0xFFE8443D) to "●" // recording dot
        StandaloneStatus.TRANSLATING -> Color(0xFFE8A33D) to "…" // ellipsis
        StandaloneStatus.SPEAKING -> Color(0xFF4CAF50) to "🔊" // speaker
        StandaloneStatus.ERROR -> MaterialTheme.colorScheme.error to "!"
    }
    Surface(
        shape = CircleShape,
        color = background,
        modifier = Modifier
            .size(120.dp)
            .clickable(onClick = onClick),
    ) {
        Box(contentAlignment = Alignment.Center, modifier = Modifier.fillMaxSize()) {
            Text(text = label, fontSize = 40.sp)
        }
    }
}

private fun statusLabel(status: StandaloneStatus): String = when (status) {
    StandaloneStatus.IDLE -> "Tap to speak"
    StandaloneStatus.LISTENING -> "Listening… tap to stop"
    StandaloneStatus.TRANSLATING -> "Translating…"
    StandaloneStatus.SPEAKING -> "Speaking translation…"
    StandaloneStatus.ERROR -> "Tap to try again"
}

@Composable
private fun StandaloneTurnCard(turn: StandaloneTurn) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text(
                text = turn.sourceLang.displayName.uppercase(),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.secondary,
            )
            Text(text = turn.sourceText, fontSize = 20.sp)
            Spacer(Modifier.height(8.dp))
            HorizontalDivider()
            Spacer(Modifier.height(8.dp))
            Text(
                text = turn.targetLang.displayName.uppercase(),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.secondary,
            )
            Text(text = turn.translatedText, fontSize = 22.sp)
        }
    }
}
