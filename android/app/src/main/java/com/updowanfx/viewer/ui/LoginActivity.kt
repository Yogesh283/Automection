package com.updowanfx.viewer.ui

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.updowanfx.viewer.data.ApiClient
import com.updowanfx.viewer.data.ApiException
import com.updowanfx.viewer.data.SessionStore
import com.updowanfx.viewer.databinding.ActivityLoginBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class LoginActivity : AppCompatActivity() {
    private lateinit var binding: ActivityLoginBinding
    private lateinit var store: SessionStore
    private lateinit var api: ApiClient

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(binding.root)

        store = SessionStore(this)
        api = ApiClient(store)

        binding.inputApiBase.setText(store.apiBase)
        binding.inputUsername.setText(store.username.orEmpty())

        if (!store.accessToken.isNullOrBlank()) {
            goSession()
            return
        }

        binding.btnLogin.setOnClickListener { signIn() }
    }

    private fun signIn() {
        val base = binding.inputApiBase.text?.toString().orEmpty().trim()
        val username = binding.inputUsername.text?.toString().orEmpty().trim()
        val password = binding.inputPassword.text?.toString().orEmpty()
        if (base.isBlank() || username.isBlank() || password.isBlank()) {
            binding.loginStatus.text = "Fill API URL, username, and password"
            return
        }

        store.apiBase = base
        binding.btnLogin.isEnabled = false
        binding.loginStatus.text = "Signing in…"

        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    api.login(username, password)
                }
                store.accessToken = result.accessToken
                store.refreshToken = result.refreshToken
                store.username = result.username
                Toast.makeText(this@LoginActivity, "Signed in as ${result.username}", Toast.LENGTH_SHORT).show()
                goSession()
            } catch (error: ApiException) {
                binding.loginStatus.text = error.message
            } catch (error: Exception) {
                binding.loginStatus.text = "Network error: ${error.javaClass.simpleName}"
            } finally {
                binding.btnLogin.isEnabled = true
            }
        }
    }

    private fun goSession() {
        startActivity(Intent(this, SessionActivity::class.java))
        finish()
    }
}
