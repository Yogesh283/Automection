package com.updowanfx.viewer.ui

import android.annotation.SuppressLint
import android.os.Bundle
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import com.updowanfx.viewer.data.SessionStore
import com.updowanfx.viewer.databinding.ActivityLiveBinding
import org.json.JSONObject

class LiveViewActivity : AppCompatActivity() {
    private lateinit var binding: ActivityLiveBinding
    private lateinit var store: SessionStore

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLiveBinding.inflate(layoutInflater)
        setContentView(binding.root)

        store = SessionStore(this)
        val sessionId = intent.getStringExtra(EXTRA_SESSION_ID) ?: store.sessionId
        val token = store.accessToken
        val apiBase = store.apiBase

        if (sessionId.isNullOrBlank() || token.isNullOrBlank()) {
            binding.liveStatus.text = "Missing session or token"
            return
        }

        val web = binding.webView
        val settings = web.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.mediaPlaybackRequiresUserGesture = false
        settings.cacheMode = WebSettings.LOAD_NO_CACHE
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW

        WebView.setWebContentsDebuggingEnabled(true)
        web.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(consoleMessage: ConsoleMessage?): Boolean {
                binding.liveStatus.text = consoleMessage?.message() ?: ""
                return true
            }
        }
        web.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                val payload = JSONObject()
                    .put("apiBase", apiBase)
                    .put("accessToken", token)
                    .put("sessionId", sessionId)
                    .toString()
                web.evaluateJavascript("window.startUpdowanLive($payload)", null)
            }
        }

        binding.liveStatus.text = "Connecting WebRTC for $sessionId…"
        web.loadUrl("file:///android_asset/liveview.html")
    }

    override fun onDestroy() {
        binding.webView.destroy()
        super.onDestroy()
    }

    companion object {
        const val EXTRA_SESSION_ID = "session_id"
    }
}
