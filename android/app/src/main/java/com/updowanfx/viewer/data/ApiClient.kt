package com.updowanfx.viewer.data

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class ApiClient(private val store: SessionStore) {
    private val jsonType = "application/json; charset=utf-8".toMediaType()
    private val http = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    data class LoginResult(
        val accessToken: String,
        val refreshToken: String,
        val userId: Int,
        val username: String,
    )

    data class StartResult(
        val sessionId: String,
        val displayName: String?,
        val profilePath: String?,
        val status: String?,
    )

    fun login(username: String, password: String): LoginResult {
        val body = JSONObject()
            .put("username", username)
            .put("password", password)
            .toString()
        val json = post("/api/auth/login", body, auth = false)
        if (!json.optBoolean("success", false)) {
            throw ApiException(json.optString("detail", "Login failed"))
        }
        val user = json.getJSONObject("user")
        return LoginResult(
            accessToken = json.getString("accessToken"),
            refreshToken = json.getString("refreshToken"),
            userId = user.getInt("id"),
            username = user.getString("username"),
        )
    }

    fun startSession(
        mobileNumber: String,
        gamePassword: String,
        startingAmount: String?,
        choice: String?,
        stopLoss: String?,
        targetProfit: String?,
    ): StartResult {
        val body = JSONObject()
            .put("mobileNumber", mobileNumber)
            .put("password", gamePassword)
        if (!startingAmount.isNullOrBlank()) body.put("startingAmount", startingAmount)
        if (!choice.isNullOrBlank()) body.put("choice", choice)
        if (!stopLoss.isNullOrBlank()) body.put("stopLoss", stopLoss)
        if (!targetProfit.isNullOrBlank()) body.put("targetProfit", targetProfit)
        body.put("maxLevels", "1")
        body.put("levelAmounts", JSONArray().put(startingAmount ?: "10"))

        val json = post("/api/session/start", body.toString(), auth = true)
        if (!json.optBoolean("success", false)) {
            throw ApiException(json.optString("detail", "Failed to start session"))
        }
        val session = json.optJSONObject("session")
        return StartResult(
            sessionId = json.getString("sessionId"),
            displayName = session?.optString("displayName"),
            profilePath = session?.optString("profilePath"),
            status = session?.optString("status"),
        )
    }

    fun stopSession(sessionId: String?) {
        val body = JSONObject()
        if (!sessionId.isNullOrBlank()) body.put("sessionId", sessionId)
        val json = post("/api/session/stop", body.toString(), auth = true)
        if (!json.optBoolean("success", false)) {
            throw ApiException(json.optString("detail", "Failed to stop session"))
        }
    }

    fun sessionStatus(): JSONObject = get("/api/session/status", auth = true)

    private fun get(path: String, auth: Boolean): JSONObject {
        val builder = Request.Builder().url(url(path)).get()
        if (auth) {
            val token = store.accessToken ?: throw ApiException("Not signed in")
            builder.header("Authorization", "Bearer $token")
        }
        return execute(builder.build())
    }

    private fun post(path: String, body: String, auth: Boolean): JSONObject {
        val builder = Request.Builder()
            .url(url(path))
            .post(body.toRequestBody(jsonType))
            .header("Content-Type", "application/json")
        if (auth) {
            val token = store.accessToken ?: throw ApiException("Not signed in")
            builder.header("Authorization", "Bearer $token")
        }
        return execute(builder.build())
    }

    private fun execute(request: Request): JSONObject {
        http.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            val json = try {
                if (text.isBlank()) JSONObject() else JSONObject(text)
            } catch (_: Exception) {
                JSONObject().put("detail", text.ifBlank { "Invalid server response" })
            }
            if (!response.isSuccessful) {
                val detail = json.optString(
                    "detail",
                    json.optString("error", "HTTP ${response.code}"),
                )
                throw ApiException(detail)
            }
            return json
        }
    }

    private fun url(path: String): String {
        val base = store.apiBase.trim().trimEnd('/')
        return base + if (path.startsWith("/")) path else "/$path"
    }
}

class ApiException(message: String) : Exception(message)
