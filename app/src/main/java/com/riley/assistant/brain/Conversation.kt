package com.riley.assistant.brain

import android.content.Context
import com.riley.assistant.data.ChatMessage
import com.riley.assistant.data.Store
import com.riley.assistant.killswitch.KillSwitch
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withContext

/** One path for everything the owner says or types, used by both the chat screen and hands-free mode. */
object Conversation {
    private val lock = Mutex()
    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    /**
     * Returns Riley's reply to speak, or null when there is nothing to say:
     * the kill switch fired, or Riley is still working on the previous message.
     */
    suspend fun handle(context: Context, input: String): String? {
        val text = input.trim()
        if (text.isEmpty() || KillSwitch.detonated) return null

        // The kill switch is a plain match on the owner's own words, checked before anything reaches the AI.
        if (KillSwitch.matches(text)) {
            withContext(Dispatchers.Main) { KillSwitch.detonate(context) }
            return null
        }

        if (!lock.tryLock()) return null
        try {
            _busy.value = true
            val store = Store.get(context)
            store.addChat(ChatMessage("user", text))
            val reply = Brain(context.applicationContext).reply()
            if (KillSwitch.detonated) return null
            store.addChat(ChatMessage(if (reply.ok) "riley" else "system", reply.text))
            return reply.text
        } finally {
            _busy.value = false
            lock.unlock()
        }
    }
}
