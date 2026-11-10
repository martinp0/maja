package cz.martinpohl.maja

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.webkit.CookieManager
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

/** Adresa serveru, volitelný read-only token pro widget a záložní upozornění. */
class SetupActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)
        val url = findViewById<EditText>(R.id.url)
        val token = findViewById<EditText>(R.id.token)
        val web = findViewById<CheckBox>(R.id.prefer_web)
        val alerts = findViewById<CheckBox>(R.id.alerts)
        val error = findViewById<TextView>(R.id.error)
        val save = findViewById<Button>(R.id.save)

        url.setText(Store.origin(this).orEmpty())
        token.setText(Store.token(this).orEmpty())
        web.isChecked = Store.preferWebView(this)
        alerts.isChecked = Store.alerts(this)

        alerts.setOnCheckedChangeListener { _, checked ->
            if (checked) askNotifications()
        }
        save.setOnClickListener {
            val origin = Origins.normalize(url.text?.toString().orEmpty())
            if (origin == null) {
                error.visibility = View.VISIBLE
                error.setText(R.string.err_url)
                return@setOnClickListener
            }
            val rawToken = token.text?.toString()?.trim().orEmpty()
            if (rawToken.isNotEmpty() && rawToken.length < 32) {
                error.visibility = View.VISIBLE
                error.setText(R.string.err_token)
                return@setOnClickListener
            }
            error.visibility = View.GONE
            val previous = Store.origin(this)
            if (previous != null && previous != origin) {
                CookieManager.getInstance().removeAllCookies(null)
            }
            Store.save(this, origin, rawToken.ifEmpty { null }, web.isChecked, alerts.isChecked)
            WatchWorker.ensure(this)
            WatchWorker.refreshNow(this)
            startActivity(
                Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP),
            )
            finish()
        }
    }

    private fun askNotifications() {
        if (Build.VERSION.SDK_INT < 33) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
    }
}
