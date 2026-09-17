package com.riley.assistant.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

val Bg = Color(0xFF0F1112)
val Panel = Color(0xFF1B1D1F)
val Accent = Color(0xFF9DB08A)
val TextMain = Color(0xFFE8E6E1)
val TextDim = Color(0xFF8C8F91)
val Danger = Color(0xFFE0645C)

enum class Screen { Chat, Tasks, Settings }

@Composable
fun RileyTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Accent,
            onPrimary = Color.Black,
            background = Bg,
            onBackground = TextMain,
            surface = Panel,
            onSurface = TextMain,
        ),
        content = content,
    )
}

@Composable
fun TopBar(current: Screen, onSelect: (Screen) -> Unit) {
    Row(
        Modifier.fillMaxWidth().background(Panel).padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("RILEY", color = Accent, fontWeight = FontWeight.Bold, fontSize = 20.sp, letterSpacing = 4.sp)
        Spacer(Modifier.weight(1f))
        Screen.entries.forEach { screen ->
            TextButton(onClick = { onSelect(screen) }) {
                Text(screen.name.uppercase(), color = if (screen == current) Accent else TextDim, letterSpacing = 1.sp)
            }
        }
    }
}

@Composable
fun DetonatedScreen() {
    Box(
        Modifier.fillMaxSize().background(Bg).safeDrawingPadding().padding(32.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            "Riley has been wiped.\nConfirm Android's uninstall prompt to remove the app.",
            color = TextDim,
            fontSize = 18.sp,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
fun SectionTitle(text: String) {
    Text(text, color = Accent, fontSize = 12.sp, letterSpacing = 3.sp, fontWeight = FontWeight.Bold)
}

@Composable
fun Field(label: String, value: String, secret: Boolean = false, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        singleLine = true,
        visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
        modifier = Modifier.fillMaxWidth(),
    )
}
