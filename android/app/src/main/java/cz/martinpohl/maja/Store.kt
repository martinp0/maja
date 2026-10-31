package cz.martinpohl.maja

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** Server, read-only token a text widgetu. Token není v logu ani v záloze cloudu. */
object Store {
    private const val FILE = "maja_secure"
    private const val ORIGIN = "origin"
    private const val TOKEN = "token"
    private const val PREFER_WEB = "prefer_web"
    private const val ALERTS = "alerts"
    private const val LAST_STATE = "last_state"
    const val W_NAME = "w_name"
    const val W_WHERE = "w_where"
    const val W_BATTERY = "w_battery"
    const val W_SEEN = "w_seen"
    const val W_ERROR = "w_error"

    fun prefs(context: Context): SharedPreferences {
        return try {
            val master = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
            EncryptedSharedPreferences.create(
                context,
                FILE,
                master,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
        } catch (err: Exception) {
            Log.w("Maja", "encrypted prefs unavailable, using private prefs")
            context.getSharedPreferences("maja_private", Context.MODE_PRIVATE)
        }
    }

    fun origin(context: Context): String? = prefs(context).getString(ORIGIN, null)?.takeIf { it.isNotBlank() }

    fun token(context: Context): String? = prefs(context).getString(TOKEN, null)?.takeIf { it.isNotBlank() }

    fun preferWebView(context: Context): Boolean = prefs(context).getBoolean(PREFER_WEB, false)

    fun alerts(context: Context): Boolean = prefs(context).getBoolean(ALERTS, false)

    fun lastState(context: Context): String? = prefs(context).getString(LAST_STATE, null)

    fun save(
        context: Context,
        origin: String,
        token: String?,
        preferWebView: Boolean,
        alerts: Boolean,
    ) {
        val previous = origin(context)
        prefs(context).edit()
            .putString(ORIGIN, origin)
            .putString(TOKEN, token.orEmpty())
            .putBoolean(PREFER_WEB, preferWebView)
            .putBoolean(ALERTS, alerts)
            .apply()
        if (previous != null && previous != origin) {
            prefs(context).edit().remove(LAST_STATE).apply()
        }
    }

    fun saveGlance(context: Context, name: String, where: String, battery: String, seen: String) {
        prefs(context).edit()
            .putString(W_NAME, name)
            .putString(W_WHERE, where)
            .putString(W_BATTERY, battery)
            .putString(W_SEEN, seen)
            .putBoolean(W_ERROR, false)
            .apply()
    }

    fun saveError(context: Context) {
        prefs(context).edit().putBoolean(W_ERROR, true).apply()
    }

    fun rememberState(context: Context, state: String) {
        prefs(context).edit().putString(LAST_STATE, state).apply()
    }
}
