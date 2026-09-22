package com.interpreter.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val InterpreterColorScheme = darkColorScheme(
    primary = Color(0xFF8AB4F8),
    secondary = Color(0xFFB0C4DE),
    background = Color(0xFF0E0E10),
    surface = Color(0xFF1A1A1D),
    onBackground = Color(0xFFF5F5F5),
    onSurface = Color(0xFFF5F5F5),
)

/** Always dark, regardless of the system theme setting - see themes.xml for why. */
@Composable
fun InterpreterTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = InterpreterColorScheme, content = content)
}
