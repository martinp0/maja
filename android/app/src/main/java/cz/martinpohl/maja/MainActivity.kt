package cz.martinpohl.maja

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.browser.customtabs.CustomTabsClient
import androidx.browser.trusted.TrustedWebActivityIntentBuilder
import com.google.androidbrowserhelper.trusted.TwaLauncher

/**
 * Ikona na ploše. HTTPS a Chrome → Trusted Web Activity, ať dál funguje Web Push
 * (geofence v reálném čase). HTTP (Tailscale) nebo volba „vždy v aplikaci“ → WebView,
 * které dostává polohu pantu z WindowManageru.
 */
class MainActivity : AppCompatActivity() {
    private var twa: TwaLauncher? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_launch)
        val origin = Store.origin(this)
        if (origin.isNullOrBlank()) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        val https = origin.startsWith("https://")
        val provider = CustomTabsClient.getPackageName(this, null)
        if (!https || Store.preferWebView(this) || provider == null) {
            openWeb()
            return
        }
        val launcher = TwaLauncher(this)
        twa = launcher
        val builder = TrustedWebActivityIntentBuilder(Uri.parse(origin))
        launcher.launch(builder, null, null) {
            finish()
        }
    }

    private fun openWeb() {
        startActivity(Intent(this, WebActivity::class.java))
        finish()
    }

    override fun onDestroy() {
        twa?.destroy()
        twa = null
        super.onDestroy()
    }
}
