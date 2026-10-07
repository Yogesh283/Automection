package com.updowanfx.viewer.data

import android.content.Context

class SessionStore(context: Context) {
    private val prefs = context.getSharedPreferences("updowanfx", Context.MODE_PRIVATE)

    var apiBase: String
        get() = prefs.getString(KEY_API, "https://updowanfx.com") ?: "https://updowanfx.com"
        set(value) = prefs.edit().putString(KEY_API, value.trim().trimEnd('/')).apply()

    var accessToken: String?
        get() = prefs.getString(KEY_ACCESS, null)
        set(value) = prefs.edit().putString(KEY_ACCESS, value).apply()

    var refreshToken: String?
        get() = prefs.getString(KEY_REFRESH, null)
        set(value) = prefs.edit().putString(KEY_REFRESH, value).apply()

    var username: String?
        get() = prefs.getString(KEY_USER, null)
        set(value) = prefs.edit().putString(KEY_USER, value).apply()

    var sessionId: String?
        get() = prefs.getString(KEY_SESSION, null)
        set(value) = prefs.edit().putString(KEY_SESSION, value).apply()

    fun clearAuth() {
        prefs.edit()
            .remove(KEY_ACCESS)
            .remove(KEY_REFRESH)
            .remove(KEY_USER)
            .remove(KEY_SESSION)
            .apply()
    }

    companion object {
        private const val KEY_API = "api_base"
        private const val KEY_ACCESS = "access_token"
        private const val KEY_REFRESH = "refresh_token"
        private const val KEY_USER = "username"
        private const val KEY_SESSION = "session_id"
    }
}
