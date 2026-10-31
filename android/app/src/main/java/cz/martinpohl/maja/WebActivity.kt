package cz.martinpohl.maja

import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.Bitmap
import android.os.Bundle
import android.view.View
import android.webkit.CookieManager
import android.webkit.GeolocationPermissions
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.net.http.SslError
import android.widget.Button
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.window.layout.FoldingFeature
import androidx.window.layout.WindowInfoTracker
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * WebView, když TWA nejde (HTTP, nebo uživatel chce pant z WindowManageru).
 * Složení displeje aktivitu neničí (`configChanges`) a do stránky posílá postoj pantu.
 */
class WebActivity : AppCompatActivity() {
    private lateinit var web: WebView
    private var lastPosture: String? = null
    private var pageReady = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val origin = Store.origin(this)
        if (origin.isNullOrBlank()) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        setContentView(R.layout.activity_web)
        web = findViewById(R.id.web)
        val error = findViewById<View>(R.id.error_panel)
        val errorText = findViewById<TextView>(R.id.error_text)
        findViewById<Button>(R.id.retry).setOnClickListener {
            error.visibility = View.GONE
            web.visibility = View.VISIBLE
            web.loadUrl(origin)
        }
        findViewById<Button>(R.id.change_server).setOnClickListener {
            startActivity(Intent(this, SetupActivity::class.java))
        }

        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) {
            WebView.setWebContentsDebuggingEnabled(true)
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            cacheMode = WebSettings.LOAD_DEFAULT
            useWideViewPort = true
            loadWithOverviewMode = false
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            mediaPlaybackRequiresUserGesture = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            userAgentString = "$userAgentString MajaAndroid/1"
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onGeolocationPermissionsShowPrompt(
                origin: String,
                callback: GeolocationPermissions.Callback,
            ) {
                callback.invoke(origin, false, false)
            }
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val scheme = request.url.scheme?.lowercase()
                return scheme != "http" && scheme != "https"
            }

            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
                pageReady = false
                error.visibility = View.GONE
                web.visibility = View.VISIBLE
            }

            override fun onPageFinished(view: WebView, url: String) {
                pageReady = true
                pushPosture()
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, err: WebResourceError) {
                if (!request.isForMainFrame) return
                web.visibility = View.GONE
                error.visibility = View.VISIBLE
                errorText.setText(R.string.err_load)
            }

            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                handler.cancel()
                web.visibility = View.GONE
                findViewById<View>(R.id.error_panel).visibility = View.VISIBLE
                errorText.setText(R.string.err_ssl)
            }
        }
        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState)
        } else {
            web.loadUrl(origin)
        }
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    if (::web.isInitialized && web.canGoBack()) web.goBack()
                    else {
                        isEnabled = false
                        onBackPressedDispatcher.onBackPressed()
                    }
                }
            },
        )
        observeFold()
    }

    private fun observeFold() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                WindowInfoTracker.getOrCreate(this@WebActivity)
                    .windowLayoutInfo(this@WebActivity)
                    .collect { info ->
                        val feature = info.displayFeatures.filterIsInstance<FoldingFeature>().firstOrNull()
                        lastPosture = postureJson(feature)
                        pushPosture()
                    }
            }
        }
    }

    private fun pushPosture() {
        if (!pageReady || !::web.isInitialized) return
        val payload = lastPosture ?: "null"
        web.evaluateJavascript(
            "window.__majaNativePosture=$payload;" +
                "(window.__majaApplyNativePosture&&window.__majaApplyNativePosture(window.__majaNativePosture))",
            null,
        )
    }

    private fun postureJson(feature: FoldingFeature?): String? {
        if (feature == null) return null
        val density = resources.displayMetrics.density.coerceAtLeast(1f)
        fun css(px: Int) = px / density
        val bounds = feature.bounds
        val state = when (feature.state) {
            FoldingFeature.State.HALF_OPENED -> "HALF_OPENED"
            else -> "FLAT"
        }
        val orientation = when (feature.orientation) {
            FoldingFeature.Orientation.VERTICAL -> "VERTICAL"
            FoldingFeature.Orientation.HORIZONTAL -> "HORIZONTAL"
            else -> "NONE"
        }
        return JSONObject()
            .put("state", state)
            .put("orientation", orientation)
            .put("hingeTop", css(bounds.top))
            .put("hingeHeight", css(bounds.height()))
            .put("hingeLeft", css(bounds.left))
            .put("hingeWidth", css(bounds.width()))
            .put("separating", feature.isSeparating)
            .toString()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::web.isInitialized) web.saveState(outState)
    }

}

