package com.riley.assistant.data

import android.content.Context

/** API keys and preferences. Stored in app-private storage; erased by the kill switch. */
class Settings(context: Context) {
    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    var callName: String
        get() = prefs.getString("call_name", null)?.takeIf { it.isNotBlank() } ?: "boss"
        set(value) = putString("call_name", value)

    var geminiKey: String
        get() = prefs.getString("gemini_key", null).orEmpty()
        set(value) = putString("gemini_key", value)

    var geminiModel: String
        get() = prefs.getString("gemini_model", null)?.takeIf { it.isNotBlank() } ?: DEFAULT_GEMINI_MODEL
        set(value) = putString("gemini_model", value)

    var groqKey: String
        get() = prefs.getString("groq_key", null).orEmpty()
        set(value) = putString("groq_key", value)

    var groqModel: String
        get() = prefs.getString("groq_model", null)?.takeIf { it.isNotBlank() } ?: DEFAULT_GROQ_MODEL
        set(value) = putString("groq_model", value)

    /** Package name of the text-to-speech engine, or "" for the tablet's default engine. */
    var ttsEngine: String
        get() = prefs.getString("tts_engine", null).orEmpty()
        set(value) = putString("tts_engine", value)

    var voiceName: String
        get() = prefs.getString("voice_name", null).orEmpty()
        set(value) = putString("voice_name", value)

    /** Whether hands-free listening should come back on when Riley is opened. */
    var handsFree: Boolean
        get() = prefs.getBoolean("hands_free", false)
        set(value) {
            prefs.edit().putBoolean("hands_free", value).apply()
        }

    var pitch: Float
        get() = prefs.getFloat("pitch", 0.8f)
        set(value) {
            prefs.edit().putFloat("pitch", value).apply()
        }

    var rate: Float
        get() = prefs.getFloat("rate", 0.95f)
        set(value) {
            prefs.edit().putFloat("rate", value).apply()
        }

    /** Calendar Riley adds meetings to; -1 = the account's primary calendar. */
    var calendarId: Long
        get() = prefs.getLong("calendar_id", -1L)
        set(value) {
            prefs.edit().putLong("calendar_id", value).apply()
        }

    var meetingAlerts: Boolean
        get() = prefs.getBoolean("meeting_alerts", true)
        set(value) {
            prefs.edit().putBoolean("meeting_alerts", value).apply()
        }

    var meetingLeadMinutes: Int
        get() = prefs.getInt("meeting_lead_minutes", 10)
        set(value) {
            prefs.edit().putInt("meeting_lead_minutes", value).apply()
        }

    var briefEnabled: Boolean
        get() = prefs.getBoolean("brief_enabled", true)
        set(value) {
            prefs.edit().putBoolean("brief_enabled", value).apply()
        }

    /** Minutes after midnight for the morning brief. Default 08:00. */
    var briefMinuteOfDay: Int
        get() = prefs.getInt("brief_minute_of_day", 8 * 60)
        set(value) {
            prefs.edit().putInt("brief_minute_of_day", value).apply()
        }

    /** Whether reminders, meeting heads-ups and the brief are also spoken out loud. */
    var speakAlerts: Boolean
        get() = prefs.getBoolean("speak_alerts", true)
        set(value) {
            prefs.edit().putBoolean("speak_alerts", value).apply()
        }

    /** "eventId:begin" keys of meetings already announced, so each one is announced once. */
    var announcedMeetings: Set<String>
        get() = prefs.getStringSet("announced_meetings", emptySet()).orEmpty().toSet()
        set(value) {
            prefs.edit().putStringSet("announced_meetings", value).apply()
        }

    @android.annotation.SuppressLint("ApplySharedPref") // must hit disk before the uninstall prompt
    fun wipe() {
        prefs.edit().clear().commit()
        appContext.deleteSharedPreferences(NAME)
    }

    private fun putString(key: String, value: String) {
        prefs.edit().putString(key, value.trim()).apply()
    }

    companion object {
        private const val NAME = "riley_settings"
        const val DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
        const val DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
    }
}
