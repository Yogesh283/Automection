package com.updowanfx.viewer.ui

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.updowanfx.viewer.data.ApiClient
import com.updowanfx.viewer.data.ApiException
import com.updowanfx.viewer.data.SessionStore
import com.updowanfx.viewer.databinding.ActivitySessionBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class SessionActivity : AppCompatActivity() {
    private lateinit var binding: ActivitySessionBinding
    private lateinit var store: SessionStore
    private lateinit var api: ApiClient

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySessionBinding.inflate(layoutInflater)
        setContentView(binding.root)

        store = SessionStore(this)
        api = ApiClient(store)

        binding.userLabel.text = "Signed in as ${store.username ?: "user"} @ ${store.apiBase}"
        refreshSessionButtons()

        binding.btnStart.setOnClickListener { startSession() }
        binding.btnStop.setOnClickListener { stopSession() }
        binding.btnLive.setOnClickListener {
            val sid = store.sessionId
            if (sid.isNullOrBlank()) {
                Toast.makeText(this, "Start a session first", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            startActivity(
                Intent(this, LiveViewActivity::class.java)
                    .putExtra(LiveViewActivity.EXTRA_SESSION_ID, sid),
            )
        }
    }

    private fun refreshSessionButtons() {
        val hasSession = !store.sessionId.isNullOrBlank()
        binding.btnLive.isEnabled = hasSession
        binding.btnStop.isEnabled = hasSession
        if (hasSession) {
            binding.sessionStatus.text = "Active session: ${store.sessionId}"
        }
    }

    private fun startSession() {
        val mobile = binding.inputMobile.text?.toString().orEmpty().trim()
        val gamePassword = binding.inputGamePassword.text?.toString().orEmpty()
        if (mobile.length < 5 || gamePassword.isBlank()) {
            binding.sessionStatus.text = "Enter game mobile and password"
            return
        }

        binding.btnStart.isEnabled = false
        binding.sessionStatus.text = "Starting session (Chrome + display)…"

        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    api.startSession(
                        mobileNumber = mobile,
                        gamePassword = gamePassword,
                        startingAmount = binding.inputStartingAmount.text?.toString(),
                        choice = binding.inputChoice.text?.toString(),
                        stopLoss = binding.inputStopLoss.text?.toString(),
                        targetProfit = binding.inputTargetProfit.text?.toString(),
                    )
                }
                store.sessionId = result.sessionId
                binding.sessionStatus.text =
                    "Session ${result.sessionId}\nDisplay ${result.displayName ?: "-"}\nStatus ${result.status ?: "running"}"
                refreshSessionButtons()
                Toast.makeText(this@SessionActivity, "Session started", Toast.LENGTH_SHORT).show()
            } catch (error: ApiException) {
                binding.sessionStatus.text = error.message
            } catch (error: Exception) {
                binding.sessionStatus.text = "Start failed: ${error.javaClass.simpleName}"
            } finally {
                binding.btnStart.isEnabled = true
            }
        }
    }

    private fun stopSession() {
        val sid = store.sessionId
        binding.btnStop.isEnabled = false
        binding.sessionStatus.text = "Stopping…"
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { api.stopSession(sid) }
                store.sessionId = null
                binding.sessionStatus.text = "Session stopped"
                refreshSessionButtons()
            } catch (error: ApiException) {
                binding.sessionStatus.text = error.message
                refreshSessionButtons()
            } catch (error: Exception) {
                binding.sessionStatus.text = "Stop failed: ${error.javaClass.simpleName}"
                refreshSessionButtons()
            }
        }
    }
}
