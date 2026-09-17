package com.riley.assistant.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.riley.assistant.data.Task
import com.riley.assistant.data.TimeUtil

@Composable
fun TasksScreen(
    tasks: List<Task>,
    onComplete: (Task) -> Unit,
    onDelete: (Task) -> Unit,
    modifier: Modifier = Modifier,
) {
    val now = System.currentTimeMillis()
    val open = tasks.filter { !it.done }.sortedBy { it.dueAt ?: Long.MAX_VALUE }
    val done = tasks.filter { it.done }.sortedByDescending { it.lastDoneAt ?: 0L }.take(20)

    LazyColumn(
        modifier.fillMaxWidth().padding(horizontal = 16.dp),
        contentPadding = PaddingValues(vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item { SectionTitle("OPEN — ${open.size}") }
        if (open.isEmpty()) item { Text("Nothing on the board. Tell Riley in chat.", color = TextDim) }
        items(open, key = { "open-${it.id}" }) { TaskRow(it, now, onComplete, onDelete) }
        if (done.isNotEmpty()) {
            item { SectionTitle("DONE") }
            items(done, key = { "done-${it.id}" }) { TaskRow(it, now, onComplete, onDelete) }
        }
    }
}

@Composable
private fun TaskRow(task: Task, now: Long, onComplete: (Task) -> Unit, onDelete: (Task) -> Unit) {
    val overdue = !task.done && task.dueAt != null && task.dueAt < now
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(Panel).padding(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                task.title,
                color = if (task.done) TextDim else TextMain,
                fontSize = 16.sp,
                textDecoration = if (task.done) TextDecoration.LineThrough else null,
            )
            val details = listOfNotNull(
                task.dueAt?.let { (if (overdue) "OVERDUE · " else "") + TimeUtil.format(it) },
                task.repeat.takeIf { it != "none" },
                task.priority.takeIf { it != "normal" }?.uppercase(),
                task.notes.takeIf { it.isNotBlank() },
            )
            if (details.isNotEmpty()) {
                Text(
                    details.joinToString(" · "),
                    color = if (overdue || task.priority == "urgent") Danger else TextDim,
                    fontSize = 13.sp,
                )
            }
        }
        if (!task.done) TextButton(onClick = { onComplete(task) }) { Text("DONE", color = Accent) }
        TextButton(onClick = { onDelete(task) }) { Text("DELETE", color = TextDim) }
    }
}
