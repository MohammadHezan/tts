@file:OptIn(ExperimentalMaterial3Api::class)

package com.interpreter.app.captionbridge

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
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.interpreter.app.standalone.SpeakerLanguage

/**
 * Listening-only: translates Zoom/Meet's own Live Captions text as it
 * appears, speaks the translation. Cannot send anything back into the call -
 * see android/README.md's Caption Bridge section for why that boundary
 * exists and what it needs from you (Zoom/Meet's own captions turned on,
 * this app's accessibility permission granted).
 */
@Composable
fun CaptionBridgeScreen(
    uiState: CaptionBridgeUiState,
    accessibilityServiceEnabled: Boolean,
    onCaptionLanguageChange: (SpeakerLanguage) -> Unit,
    onOpenAccessibilitySettings: () -> Unit,
    onSwitchMode: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Caption Bridge") },
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
            Text(
                text = "Translates Zoom/Meet's own Live Captions as they appear and speaks the " +
                    "translation. It cannot send your reply into the call - speak for yourself, " +
                    "or use Standalone mode to translate what you say and say it yourself.",
                fontSize = 13.sp,
                color = MaterialTheme.colorScheme.secondary,
            )
            Spacer(Modifier.height(12.dp))

            AccessibilityStatusRow(accessibilityServiceEnabled, onOpenAccessibilitySettings)
            Spacer(Modifier.height(12.dp))

            Text("The other person is speaking:", fontSize = 13.sp, color = MaterialTheme.colorScheme.secondary)
            Spacer(Modifier.height(4.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SpeakerLanguage.entries.forEach { language ->
                    FilterChip(
                        selected = uiState.captionLanguage == language,
                        onClick = { onCaptionLanguageChange(language) },
                        label = { Text(language.displayName) },
                    )
                }
            }
            Spacer(Modifier.height(12.dp))

            uiState.errorMessage?.let { message ->
                Text(text = message, fontSize = 13.sp, color = MaterialTheme.colorScheme.error)
                Spacer(Modifier.height(8.dp))
            }

            if (uiState.rawDetectedText.isNotBlank()) {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
                    Column(Modifier.padding(12.dp)) {
                        Text(
                            text = "RAW DETECTED TEXT (debug - confirms it's reading the right thing)",
                            fontSize = 10.sp,
                            color = MaterialTheme.colorScheme.secondary,
                        )
                        Text(text = uiState.rawDetectedText, fontSize = 14.sp)
                    }
                }
                Spacer(Modifier.height(12.dp))
            }

            HorizontalDivider()

            LazyColumn(
                modifier = Modifier.fillMaxWidth(),
                contentPadding = PaddingValues(vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(uiState.lines.reversed()) { line ->
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Text(text = line.original, fontSize = 16.sp, color = MaterialTheme.colorScheme.secondary)
                            Spacer(Modifier.height(6.dp))
                            Text(text = line.translated, fontSize = 22.sp)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AccessibilityStatusRow(enabled: Boolean, onOpenSettings: () -> Unit) {
    if (enabled) {
        Text(
            text = "Accessibility permission: on",
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.primary,
        )
    } else {
        Column {
            Text(
                text = "Accessibility permission needed - required to read Zoom/Meet's caption text.",
                fontSize = 13.sp,
                color = MaterialTheme.colorScheme.error,
                textAlign = TextAlign.Start,
            )
            Spacer(Modifier.height(6.dp))
            Button(onClick = onOpenSettings) { Text("Open accessibility settings") }
        }
    }
}
