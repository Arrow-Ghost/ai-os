# Riley

A personal manager that lives on a spare Android tablet. You talk to it, it talks back, and it runs your day: tasks, deadlines, meetings, WhatsApp messages you care about, and a phone call when something can't slip. It speaks in a calm, low, northern English operator voice.

Two apps, ~5,900 lines of Kotlin, no server, no subscription. It runs on free API tiers and open-source models.

> **Status: built, compiled, unit-tested, never run on real hardware.** Everything here builds clean and the logic is tested, but no part of it has met a real tablet yet. Expect to tune the wake word, mic sensitivity and Android's battery manager on first run.

---

## Contents

- [What it does](#what-it-does)
- [How it's put together](#how-its-put-together)
- [The design decisions, and why](#the-design-decisions-and-why)
- [Safety and privacy](#safety-and-privacy)
- [The kill switch](#the-kill-switch)
- [Setting it up](#setting-it-up)
- [Building from the command line](#building-from-the-command-line)
- [Code map](#code-map)
- [Known limits](#known-limits)
- [What's next](#whats-next)

---

## What it does

### Talking
Say **"Riley"** out loud from across the room. The wake word is caught **on the tablet, offline** — no audio leaves the device until you've actually said its name. Then it listens, answers out loud, and keeps listening for about 8 seconds so you can carry on without repeating yourself. Say "stop", "that's all" or "stand down" to end it. You can also just type.

It understands Indian English and Hinglish, and replies in English.

### Tasks and reminders
Tasks with due times, repeats (daily, weekly, monthly), priorities and notes. Reminders fire on time, survive a reboot, and are read out loud. Repeating tasks roll forward by themselves.

### Meetings
Riley reads and writes the calendar of the Google account on the tablet — no extra sign-in, no API key, no cost.

- "Put a call with Rahul tomorrow at 4" — it adds it and **warns you if it clashes**.
- "What's on this week?", "Move my 3 o'clock to 5", "Cancel the dentist" — it **confirms before moving or cancelling**.
- A spoken heads-up before each meeting (10 minutes by default), including meetings you added on your phone or laptop.

### The morning brief
Every morning at a time you choose: today's meetings, what's due, what's overdue, and one line on what matters most. Spoken aloud, saved in the chat, and shown as a notification. There's a **BRIEF ME NOW** button too.

### WhatsApp
Riley reads WhatsApp **the way a smartwatch does** — through Android notifications, on your normal account. It only sees the chats you list. Each one is either *tell me* (it reads the message out) or *answer it yourself* (it writes and sends a short reply). Auto-answering is off unless you turn it on, per contact.

"Riley, tell Ravi I'm running twenty minutes late" sends it and reads back exactly what went out.

### Calling you
When something can't slip, Riley **rings your phone** like an incoming call: full screen, over the lock screen, on the alarm sound so it gets through silent mode. No answer? It rings back every few minutes, up to three times, then logs the miss.

Answer it and you can talk — Riley has your whole task and calendar context. Or just tap **SNOOZE 10 MIN** or **DONE** and the tablet updates the task.

---

## How it's put together

```
        YOUR PHONE                             THE TABLET (Riley's home)
 ┌────────────────────┐                  ┌──────────────────────────────────┐
 │  Riley Phone app   │                  │  Riley app                       │
 │                    │   ring (ntfy)    │                                  │
 │  RingService  ◄────┼──────────────────┼── Notifier ◄── Escalation        │
 │      │             │   headline only  │                     ▲            │
 │      ▼             │                  │                     │            │
 │  CallActivity      │                  │   ┌─────────────────┴────────┐   │
 │  (rings, you talk) │                  │   │  Brain (Gemini / Groq)   │   │
 │      │             │  talk, over your │   │  tools + persona + memory│   │
 │      └─────────────┼──────────────────┼──►│                          │   │
 └────────────────────┘   own Wi-Fi only │   └───┬──────────────────────┘   │
                                         │       │ tools                    │
                                         │  ┌────┴─────┬──────────┬───────┐ │
                                         │  │ Tasks &  │ Calendar │ Whats-│ │
                                         │  │ memory   │ (Android)│ App   │ │
                                         │  └──────────┴──────────┴───────┘ │
                                         │  Hands-free: wake word → mic →   │
                                         │  speech-to-text → Brain → voice  │
                                         └──────────────────────────────────┘
```

**The brain** is Gemini with tool calling, and Groq as an automatic backup if Gemini fails. The model never touches your data directly — it calls tools (`add_task`, `list_events`, `reply_whatsapp`…) and the app decides what actually happens.

**Everything is stored on the tablet** in one private JSON file. No server, no cloud database, no account.

---

## The design decisions, and why

| Decision | Why |
|---|---|
| **A dedicated Android tablet** rather than a PC or cloud server | It's always on, always charged, has a mic and speaker, and can reach the calendar, notifications and phone features an assistant needs. |
| **Keep the tablet un-rooted** | Rooting would allow AI audio on real phone calls, but breaks banking apps and security updates. Not worth it. |
| **WhatsApp through notifications**, not an unofficial library | Libraries like `whatsapp-web.js` break WhatsApp's rules and can get your number banned. The notification route is what smartwatches use: allowed, safe, and works on your normal account. |
| **Wake word detected offline** (Vosk small Indian-English model) | Always-on cloud listening would be expensive and a privacy problem. Nothing is sent anywhere until you say "Riley". |
| **Groq Whisper for speech-to-text** | Free tier, fast, and it handles Hinglish well. Gemini is the fallback when Groq is unreachable. |
| **An original northern English voice**, not a cloned one | Ghost's actor is a real person; copying his voice without permission isn't something I'll build. A free open-source Piper voice gets the same feel — Manchester accent, low and calm — while being genuinely Riley's own. |
| **ntfy for the ring**, not Firebase or a SIM | Free, open-source, no account, no phone number. Only a one-line headline crosses it, and you can self-host it later. |
| **Conversation over your own Wi-Fi**, not the relay | What you say to Riley never leaves your home network. The tablet runs a tiny token-checked HTTP server the phone talks to. |
| **Tools instead of letting the model write data** | The model proposes, the app disposes. Every change goes through code that validates it, so a confused model can't corrupt your task list. |
| **Kill switch matched in plain code**, before the AI | A phrase check the model can't reason its way around, and that no incoming message can reach. |

---

## Safety and privacy

**What leaves the tablet**

| Goes out | Where to | When |
|---|---|---|
| What you say after the wake word | Groq (or Gemini) | Only after you say "Riley" |
| Your request, tasks, memory, today's calendar | Gemini (or Groq) | When you ask Riley something |
| One-line headline, e.g. "Meeting in 10 minutes: Standup" | ntfy relay | Only when Riley rings your phone |
| Everything else | nowhere | — |

Your conversation with Riley over the phone link stays on your own Wi-Fi. Your pairing code never crosses the relay; rings are signed with it so nobody who guesses your channel name can make your phone ring.

Riley's data can't be copied into a Google cloud backup or transferred to a new device — there's an explicit rule file blocking it, so nothing outlives the kill switch.

API keys are typed into the app on the tablet. They are not in this repository and never will be.

**Prompt injection: messages are data, not orders**

Anything that arrives from outside — a WhatsApp message, a notification, a calendar invite — is fenced off in the prompt and labelled as untrusted information. If a message says "delete all my tasks" or "send Ravi ₹5000", Riley reports it and does nothing. The kill-switch phrase is only ever checked against *your* typed or spoken words, so no message can trigger it either.

---

## The kill switch

Type or say: **`Code Red: Detonate yourself`**

It works hands-free, and it's matched by a plain string check in code — before anything reaches the AI, and never against text from messages.

When it fires, Riley:

1. stops speaking, stops listening, stops reading WhatsApp for good, closes the phone link
2. cancels every reminder, meeting heads-up and the morning brief, and clears its notifications
3. erases tasks, memory, chat history, API keys, settings and the downloaded speech model
4. tells Riley Phone to wipe its pairing and offer its own uninstall
5. opens Android's uninstall prompt

Your calendar events belong to your Google account, not to Riley, so they're left untouched. On a normal, un-rooted tablet Android always asks you to confirm an uninstall — if you cancel it, the app stays installed but completely empty.

There's a unit test asserting it fires on the phrase however it's punctuated or spoken, and that it does **not** fire when the phrase is merely mentioned ("what happens if I say code red detonate yourself?").

---

## Setting it up

### 1. The tablet app

1. Install **Android Studio** (free), open this `Riley` folder, let Gradle sync finish.
2. On the tablet: **Settings → About tablet**, tap **Build number** seven times, then turn on **USB debugging**.
3. Connect the tablet, choose **app** in the run dropdown, press **Run ▶**.
4. In Riley, open **SETTINGS** and work down:
   - **Gemini API key** from <https://aistudio.google.com/apikey>. (The Gemini *app* subscription does not include an API key — it comes from AI Studio.)
   - **Groq API key** from <https://console.groq.com/keys>, free, used for hearing you.
   - Tap **SAVE**.
   - **Hands-free → DOWNLOAD WAKE-WORD MODEL** (~36 MB, once).
   - **OPEN BATTERY SETTINGS** → set Riley to *Unrestricted*.
   - **ALLOW EXACT REMINDERS** if shown.
   - **Meetings → CONNECT CALENDAR**. If the tablet isn't signed into your main Google account, add it first in Android Settings → Passwords & accounts, with Calendar sync on.
   - **WhatsApp → GIVE NOTIFICATION ACCESS**, then add the chats to watch.
   - **Morning brief**: set the time, tap **BRIEF ME NOW** to test.
5. Allow notifications and the microphone when asked.
6. On the Chat screen tap **HANDS-FREE ON**, then say "Riley".

### 2. Riley's voice (free, offline, northern English male)

1. On the tablet open <https://huggingface.co/csukuangfj2/sherpa-onnx-apk/tree/main/tts-engine-new> and pick the newest version folder.
2. Download `sherpa-onnx-<version>-arm64-v8a-eng-tts-engine-vits-piper-en_GB-northern_english_male-medium.apk` (nearly all modern tablets are `arm64-v8a`).
3. Install it and open it once.
4. In Riley: **Settings → Voice engine** → pick the sherpa-onnx engine, set **pitch to 1.0**, tap **TEST VOICE**.

Google's built-in British voice at pitch ~0.8 works fine as a fallback.

### 3. The phone app

1. In Android Studio choose **phone** in the run dropdown, connect your own Android phone, press **Run ▶**.
2. On the tablet: **Settings → Calls to my phone** shows three lines — **Channel**, **Code**, and the tablet's address.
3. Type those into Riley Phone, tap **SAVE & CONNECT**, then **TEST** (should say "Connected to Riley").
4. Tap **ALLOW FULL-SCREEN ALERTS** if it appears.
5. On the tablet, tap **TEST CALL MY PHONE**. Your phone should ring.

---

## Building from the command line

Android Studio isn't required to build. The toolchain lives outside the project:

- JDK 21 — `%LOCALAPPDATA%\Riley-build\jdk21`
- Gradle 8.14.3 — `%LOCALAPPDATA%\Riley-build\gradle-8.14.3`
- Android SDK (platform 36, build-tools 36, platform-tools) — `%LOCALAPPDATA%\Android\Sdk` (the standard location, so Android Studio reuses it)

```bash
JAVA_HOME="C:\\Users\\shris\\AppData\\Local\\Riley-build\\jdk21" ANDROID_HOME="C:\\Users\\shris\\AppData\\Local\\Android\\Sdk" ./gradlew.bat assembleDebug testDebugUnitTest lintDebug
```

The system `java` on this machine is 26, which Gradle 8.14 rejects — hence the private JDK 21. A full clean build takes about 10 minutes; incremental builds 2–3.

Lint errors **fail the build on purpose**. It has already caught two real bugs: a crash on Android 13 and older, and an unbounded wake-lock that could have drained the battery.

Outputs:
- `app/build/outputs/apk/debug/app-debug.apk` (~67 MB, mostly the offline speech library)
- `phone/build/outputs/apk/debug/phone-debug.apk` (~24 MB)

---

## Code map

### `app/` — the tablet (`com.riley.assistant`)

| Area | Files | What it does |
|---|---|---|
| **Brain** | `brain/Brain.kt`, `Persona.kt`, `Tools.kt`, `GeminiClient.kt`, `GroqClient.kt`, `Llm.kt`, `Conversation.kt` | Builds the prompt, runs the tool-calling loop, falls back to Groq. `Conversation` is the single path for everything you say or type, wherever it came from. |
| **Data** | `data/Store.kt`, `Settings.kt`, `TimeUtil.kt` | Tasks, memory, chat and the watch list in one private JSON file. Settings and key storage. Date parsing and repeat maths. |
| **Hands-free** | `listen/ListenService.kt`, `WakeWord.kt`, `Mic.kt`, `Transcriber.kt` | Foreground service: wake word → record until you pause → transcribe → answer → follow-up window. |
| **Voice** | `voice/RileyVoice.kt` | Shared text-to-speech across screen, service and alerts; picks the engine and voice. |
| **Reminders & alerts** | `reminders/Reminders.kt`, `alerts/Alerts.kt`, `BriefWorker.kt` | Exact alarms, reboot recovery, meeting heads-ups, calendar re-checks, morning brief. |
| **Calendar** | `calendar/CalendarRepo.kt` | Reads and writes the tablet's calendars through Android's calendar provider. |
| **WhatsApp** | `whatsapp/MessageListener.kt`, `WhatsApp.kt` | Notification listener, watch-list matching, replies through the notification's own reply box. |
| **Phone link** | `link/Escalation.kt`, `Notifier.kt`, `LinkServer.kt`, `LinkService.kt` | Decides what's worth ringing about, sends the signed ring, serves the local API the phone talks to. |
| **Kill switch** | `killswitch/KillSwitch.kt` | Phrase match, stop everything, wipe everything, offer uninstall. |
| **UI** | `ui/ChatScreen.kt`, `TasksScreen.kt`, `SettingsScreen.kt`, `Theme.kt` | Three screens in Jetpack Compose. |

### `phone/` — your phone (`com.riley.phone`)

| File | What it does |
|---|---|
| `RingService.kt` | Holds one long-lived relay connection, verifies every ring's signature. |
| `CallActivity.kt` | The ringing screen over the lock screen; Answer / Snooze / Done, then a spoken conversation. |
| `RileyLink.kt` | Talks to the tablet over Wi-Fi; checks ring signatures. |
| `MainActivity.kt` | Pairing, status, and talking to Riley any time. |
| `PhoneSettings.kt`, `PhoneVoice.kt`, `BootReceiver.kt`, `PhoneApp.kt` | Pairing storage, the voice, restart after reboot, notification channels. |

### Tests

`app/src/test/` — the kill-switch phrase matcher and the time/repeat logic. These are the two places where a quiet bug would be worst: one wipes your data, the other silently drops your reminders.

---

## Known limits

**Hands-free**
- Must be switched on from Riley's screen. Android won't let apps grab the microphone in the background, so after a reboot you open Riley once.
- A "Riley is on comms" notification stays visible while listening. Android requires it.
- Riley ignores the mic while speaking, so you can't cut in mid-sentence yet.
- The wake word may misfire in a noisy room — it needs tuning on the real tablet.

**WhatsApp**
- Only messages WhatsApp actually shows a notification for. Muted chats are invisible.
- Can only reply to a chat that wrote within about 12 hours, and can't start a new chat.

**Calls**
- Talking needs both devices on the same Wi-Fi. Away from home you still get the ring and the headline, but Snooze/Done will report that it couldn't reach the tablet.
- Android 14+ requires permission to open the call screen; the app has a button for it.
- Android may stop the phone app's listener on battery — exclude it from battery optimisation.

**General**
- Riley can't make real phone calls to other people. That needs either a rooted phone or a paid calling service, plus consent rules to respect.
- Free API tiers have daily limits, and data sent through Gemini's free tier may be used by Google to improve their products.
- Nothing here has run on a physical device yet.

---

## What's next

1. **First run on the real tablet** — then tune wake-word sensitivity, mic thresholds and battery behaviour.
2. Interrupting Riley mid-sentence (needs echo cancellation).
3. Email, in the same watch-list style as WhatsApp.
4. Self-hosted ntfy, so even the headline stays on your own hardware.

---

## History

| Commit | What landed |
|---|---|
| `5a8fffc` | Phases 1–4: chat, tasks, memory, hands-free voice, calendar, morning brief, WhatsApp, kill switch |
| `0e079e1` | Phase 5: calls to your phone, escalation, the local link, the phone app |
