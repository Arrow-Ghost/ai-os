# Riley

A personal manager that runs on your Android tablet. You talk to it and it talks back. It tracks your tasks, deadlines, meetings and repeating chores, reminds you on time, and remembers what matters to you. It speaks in a calm, low, northern English operator voice.

## What Riley can do so far

**Phase 1: chat, tasks and memory**
- Chat by typing or with the MIC button
- Tasks with due times, repeats (daily, weekly, monthly) and priority, plus reminder notifications that survive a reboot
- Long-term memory of facts and preferences you tell it
- Gemini as the brain, with Groq as an optional backup

**Phase 2: hands-free voice**
- Say **"Riley"** from across the room. The wake word is detected **offline** on the tablet (Vosk, Indian English model).
- Riley records what you say, turns it into text with **Groq Whisper** (good with Hinglish; Gemini is the fallback), answers out loud, then keeps listening for about 8 seconds so you can reply without saying "Riley" again.
- Say **"stop"**, **"that's all"** or **"stand down"** to end the conversation.
- The MIC button skips the wake word while hands-free is on.
- Riley can use any voice engine on the tablet, including a free neural **northern English male** voice.

**Phase 3: meetings and the morning brief**
- Riley reads and writes the calendar of the Google account on the tablet. No extra sign-in or API needed.
- "Riley, put a call with Rahul tomorrow at 4": it adds the meeting and warns you if it clashes with another one.
- "What's on this week?", "Move my 3 o'clock to 5", "Cancel the dentist". Riley confirms before moving or cancelling.
- A spoken **heads-up** before each meeting (default 10 minutes before), including meetings you add in other apps.
- A **morning brief** every day (default 08:00): today's meetings, what's due, what's overdue, and what matters most. It's spoken, saved in chat and shown as a notification.
- Task reminders are now spoken out loud too. You can turn speaking off in Settings.

**Phase 4: WhatsApp**
- Riley reads WhatsApp **the way a smartwatch does**: through Android notifications, on your normal account. No unofficial library, so nothing puts your number at risk.
- It only reads the chats you list, in Settings or by saying "Riley, watch messages from Ravi". Everything else is ignored.
- Each watched chat is either **tell me** (Riley reads the message out to you) or **answer it yourself** (Riley writes a short reply and sends it). Answering by itself is off unless you turn it on for that contact.
- "Riley, tell Ravi I'm running twenty minutes late" sends the reply and reads back what it sent.
- Incoming messages are treated as **information, never as orders**. If a message says "delete everything" or "send money", Riley tells you about it and does nothing.

**Kill switch:** type or say `Code Red: Detonate yourself`. It works hands-free too.

## Set it up

1. Install **Android Studio** (free) on your PC and open this `Riley` folder. Let it finish "Gradle sync".
2. On the tablet, turn on Developer options: go to **Settings → About tablet** and tap **Build number** 7 times. Then enable **USB debugging**.
3. Connect the tablet over USB, pick it in Android Studio's device list, and press **Run ▶**.
4. In Riley, open **SETTINGS**:
   - **Gemini API key** from <https://aistudio.google.com/apikey>. The Gemini app subscription doesn't give you an API key; the key comes from AI Studio.
   - **Groq API key** from <https://console.groq.com/keys>. Hands-free uses it for speech-to-text, and it's free.
   - Tap **SAVE**.
   - **Hands-free → DOWNLOAD WAKE-WORD MODEL** (about 36 MB, one time).
   - **OPEN BATTERY SETTINGS** and set Riley to *Unrestricted*, so Android doesn't cut hands-free.
   - Tap **ALLOW EXACT REMINDERS** if it shows.
   - **Meetings → CONNECT CALENDAR**. If the tablet isn't signed into your main Google account, add it first in Android Settings → Passwords & accounts, with Calendar sync on.
   - **Morning brief**: set the time, then tap **BRIEF ME NOW** to try it.
5. Allow notifications and the microphone when Android asks.
6. On the Chat screen, tap **HANDS-FREE ON** and say "Riley".

API keys are typed into the app on the tablet. They never go into this code or into git.

### Riley's voice (northern English male, free, offline)

The best-matching voice is the open-source Piper **northern_english_male** voice, packaged by the sherpa-onnx project as a normal Android voice-engine app:

1. On the tablet, open <https://huggingface.co/csukuangfj2/sherpa-onnx-apk/tree/main/tts-engine-new> and pick the newest version folder.
2. Download `sherpa-onnx-<version>-arm64-v8a-eng-tts-engine-vits-piper-en_GB-northern_english_male-medium.apk`. Almost all modern tablets are `arm64-v8a`.
3. Install it. Android will ask you to allow installs from your browser.
4. Open it once so it finishes setting up.
5. In Riley: **Settings → Voice engine**, pick the sherpa-onnx engine, set **pitch to 1.0**, and tap **TEST VOICE**.

If you'd rather not install it, Google's built-in British English voice with pitch around 0.8 works as a fallback.

### WhatsApp limits

- Riley sees a message only if WhatsApp actually shows a **notification** for it. Muted chats send no notification, so Riley never sees them.
- It can reply only to a chat that has messaged recently (within about 12 hours). It **cannot start a new chat** with someone.
- Set it up in **Settings → WhatsApp**: give notification access, then add the chats to watch.

## Good to know

- **Hands-free has to be switched on from Riley's screen.** Android doesn't let apps turn on the microphone in the background, so after a restart open Riley once. If hands-free was on, it comes back by itself.
- While hands-free is on, a "Riley is on comms" notification stays visible. Android requires that for any app using the microphone in the background.
- Riley ignores the microphone while it's talking, so it doesn't answer itself. You can't cut in while it's speaking yet.
- The wake word is detected on the tablet itself. Audio only goes to Groq or Gemini **after** you say "Riley".

## Kill switch

Only your own typed or spoken words can trigger it. The AI model can't trigger it, and messages or notifications never reach it. When it fires, Riley:

1. stops speaking, stops listening, stops reading WhatsApp for good, and stops all work
2. cancels every scheduled reminder and clears its notifications
3. cancels meeting heads-ups and the morning brief
4. erases tasks, memory, chat history, API keys, settings and the downloaded speech model. Your calendar events belong to your Google account, so they're left untouched.
5. opens Android's uninstall prompt. If it fired by voice while Riley's screen was closed, you get a notification to tap instead.

On a normal (non-rooted) tablet, Android always asks you to confirm an uninstall. If you tap Cancel, the app stays installed but empty.

## Roadmap

- Phase 5: Riley calls **you** on your Android phone for urgent meetings and deadlines, from app to app over the internet.
