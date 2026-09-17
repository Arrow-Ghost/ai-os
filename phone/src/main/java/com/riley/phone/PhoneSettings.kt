package com.riley.phone

import android.content.Context

/** Everything this app knows: the pairing details for one tablet. */
class PhoneSettings(context: Context) {
    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    /** The secret channel the tablet rings on. */
    var topic: String
        get() = prefs.getString("topic", null).orEmpty()
        set(value) = put("topic", value)

    /** Shared code: proves a ring is really from the tablet, and lets this app talk to it. */
    var code: String
        get() = prefs.getString("code", null).orEmpty()
        set(value) = put("code", value)

    /** The tablet's address on the home Wi-Fi, e.g. 192.168.1.20:8787. Updated by each ring. */
    var tablet: String
        get() = prefs.getString("tablet", null).orEmpty()
        set(value) = put("tablet", value)

    var pushServer: String
        get() = prefs.getString("push_server", null)?.takeIf { it.isNotBlank() } ?: "https://ntfy.sh"
        set(value) = put("push_server", value.trimEnd('/'))

    val paired: Boolean get() = topic.isNotBlank() && code.isNotBlank()

    @android.annotation.SuppressLint("ApplySharedPref") // must hit disk before the uninstall prompt
    fun wipe() {
        prefs.edit().clear().commit()
        appContext.deleteSharedPreferences(NAME)
    }

    private fun put(key: String, value: String) {
        prefs.edit().putString(key, value.trim()).apply()
    }

    private companion object {
        const val NAME = "riley_phone"
    }
}
