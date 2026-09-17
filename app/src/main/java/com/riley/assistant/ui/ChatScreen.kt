package com.riley.assistant.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.riley.assistant.data.ChatMessage
import com.riley.assistant.listen.ListenState

@Composable
fun ChatScreen(
    messages: List<ChatMessage>,
    busy: Boolean,
    listenState: ListenState,
    listenError: String?,
    onSend: (String) -> Unit,
    onMic: () -> Unit,
    onToggleHandsFree: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var input by rememberSaveable { mutableStateOf("") }
    val listState = rememberLazyListState()
    LaunchedEffect(messages.size, busy) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.size - 1)
    }

    Column(modifier.fillMaxWidth()) {
        HandsFreeBar(listenState, listenError, onToggleHandsFree)
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 16.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (messages.isEmpty()) {
                item { Text("Riley standing by. Tell me what's on your plate.", color = TextDim) }
            }
            items(messages) { MessageBubble(it) }
            if (busy) {
                item { Text("Riley is on it…", color = TextDim, fontStyle = FontStyle.Italic) }
            }
        }
        Row(
            Modifier.fillMaxWidth().background(Panel).padding(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Message Riley") },
                maxLines = 4,
            )
            Spacer(Modifier.width(8.dp))
            OutlinedButton(onClick = onMic) { Text("MIC") }
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = {
                    val text = input
                    input = ""
                    onSend(text)
                },
                enabled = input.isNotBlank(),
            ) { Text("SEND") }
        }
    }
}

@Composable
private fun HandsFreeBar(state: ListenState, error: String?, onToggle: () -> Unit) {
    val on = state != ListenState.Off
    val dot = when (state) {
        ListenState.Off -> TextDim
        ListenState.Listening, ListenState.Speaking -> Accent
        else -> Color(0xFF6E7F62)
    }
    Row(
        Modifier.fillMaxWidth().background(Color(0xFF15181A)).padding(horizontal = 16.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(dot))
        Spacer(Modifier.width(10.dp))
        Text(
            if (!on && error != null) error else state.label,
            color = if (!on && error != null) Danger else TextDim,
            fontSize = 13.sp,
            modifier = Modifier.weight(1f),
        )
        TextButton(onClick = onToggle) {
            Text(if (on) "HANDS-FREE OFF" else "HANDS-FREE ON", color = Accent, letterSpacing = 1.sp)
        }
    }
}

@Composable
private fun MessageBubble(message: ChatMessage) {
    val mine = message.role == "user"
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        Column(
            Modifier
                .widthIn(max = 600.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(if (mine) Color(0xFF26352A) else Panel)
                .padding(12.dp),
        ) {
            val label = when (message.role) {
                "user" -> "YOU"
                "riley" -> "RILEY"
                "whatsapp" -> "WHATSAPP"
                else -> "SYSTEM"
            }
            val labelColour = when (message.role) {
                "system" -> Danger
                "whatsapp" -> Color(0xFF7FB37F)
                else -> Accent
            }
            Text(label, color = labelColour, fontSize = 11.sp, letterSpacing = 2.sp)
            Text(message.text, color = TextMain, fontSize = 16.sp)
        }
    }
}
