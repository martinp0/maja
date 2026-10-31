package cz.martinpohl.maja

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import cz.martinpohl.maja.widget.DogWidget
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant
import java.util.concurrent.TimeUnit

/**
 * Záloha k Web Push. WorkManager neumí častěji než ~15 minut, takže útěk psa včas
 * hlásí pořád odběr v TWA (Chrome). Tohle drží widget na krytu Flipu a dorazí změnu,
 * když push není zapnutý.
 */
class WatchWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val origin = Store.origin(applicationContext)
        val token = Store.token(applicationContext)
        if (origin.isNullOrBlank() || token.isNullOrBlank()) {
            DogWidget.push(applicationContext)
            return Result.success()
        }
        return try {
            val snap = fetch(origin, token)
            Store.saveGlance(applicationContext, snap.name, snap.where, snap.battery, snap.seen)
            if (Store.alerts(applicationContext)) notifyIfChanged(snap)
            DogWidget.push(applicationContext)
            Result.success()
        } catch (err: Exception) {
            Log.i(TAG, "glance refresh failed (${err.javaClass.simpleName})")
            Store.saveError(applicationContext)
            DogWidget.push(applicationContext)
            Result.retry()
        }
    }

    private fun notifyIfChanged(snap: Snap) {
        val key = snap.stateKey ?: return
        val previous = Store.lastState(applicationContext)
        Store.rememberState(applicationContext, key)
        if (previous == null || previous == key) return
        if (!canNotify()) return
        val text = when {
            snap.escaped -> applicationContext.getString(R.string.notify_escaped, snap.name)
            snap.state == "home" -> applicationContext.getString(R.string.notify_home, snap.name)
            snap.state == "away" -> applicationContext.getString(R.string.notify_away, snap.name)
            else -> return
        }
        val nm = applicationContext.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, applicationContext.getString(R.string.channel_geofence), NotificationManager.IMPORTANCE_HIGH),
            )
        }
        val pending = PendingIntent.getActivity(
            applicationContext,
            0,
            Intent(applicationContext, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_pin)
            .setContentTitle(snap.name)
            .setContentText(text)
            .setContentIntent(pending)
            .setAutoCancel(true)
            .build()
        nm.notify(NOTIFY_ID, notification)
    }

    private fun canNotify(): Boolean {
        if (Build.VERSION.SDK_INT < 33) return true
        return ContextCompat.checkSelfPermission(
            applicationContext,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
    }

    private data class Snap(
        val name: String,
        val where: String,
        val battery: String,
        val seen: String,
        val state: String?,
        val escaped: Boolean,
        val stateKey: String?,
    )

    private fun fetch(origin: String, token: String): Snap {
        val trackers = getJson(origin, token, "/api/trackers")
        val arr = trackers as? JSONArray ?: JSONArray()
        val first = if (arr.length() > 0) arr.getJSONObject(0) else null
        val id = first?.optString("tracker_id").orEmpty()
        val pets = getJson(origin, token, "/api/pets") as? JSONArray
        var name = first?.optString("pet_name").orEmpty()
        if (pets != null) {
            for (i in 0 until pets.length()) {
                val pet = pets.getJSONObject(i)
                if (pet.optString("tracker_id") == id && pet.optString("name").isNotBlank()) {
                    name = pet.optString("name")
                }
            }
        }
        if (name.isBlank()) name = applicationContext.getString(R.string.app_name)
        val latest = if (id.isNotEmpty()) getJson(origin, token, "/api/trackers/$id/latest") as? JSONObject else null
        val hw = latest?.optJSONObject("hw")
        val pos = latest?.optJSONObject("position")
        val batteryPct = if (hw != null && !hw.isNull("battery_pct")) hw.optInt("battery_pct") else null
        val battery = if (batteryPct == null) {
            applicationContext.getString(R.string.battery_unknown)
        } else {
            applicationContext.getString(R.string.battery_pct, batteryPct)
        }
        val seen = relative(pos?.optString("ts"))
        val geo = getJson(origin, token, "/api/geofence/state") as? JSONObject
        val enabled = geo?.optBoolean("enabled") == true
        val state = geo?.optString("state")
        val escaped = geo?.optBoolean("escaped") == true
        val where = if (!enabled || state.isNullOrBlank() || state == "unknown") {
            applicationContext.getString(R.string.where_unknown)
        } else if (escaped) {
            applicationContext.getString(R.string.where_escaped)
        } else if (state == "home") {
            applicationContext.getString(R.string.where_home)
        } else {
            val dist = if (geo.isNull("distance_m")) null else geo.optInt("distance_m")
            if (dist == null) applicationContext.getString(R.string.where_away)
            else applicationContext.getString(R.string.where_away_m, dist)
        }
        val key = if (enabled && !state.isNullOrBlank() && state != "unknown") "$state:$escaped" else null
        return Snap(name, where, battery, seen, state, escaped, key)
    }

    private fun relative(iso: String?): String {
        if (iso.isNullOrBlank()) return applicationContext.getString(R.string.seen_never)
        val then = try {
            Instant.parse(iso).toEpochMilli()
        } catch (_: Exception) {
            return applicationContext.getString(R.string.seen_never)
        }
        val min = ((System.currentTimeMillis() - then) / 60_000L).coerceAtLeast(0)
        return when {
            min < 1 -> applicationContext.getString(R.string.seen_now)
            min < 60 -> applicationContext.getString(R.string.seen_min, min)
            else -> applicationContext.getString(R.string.seen_h, min / 60)
        }
    }

    private fun getJson(origin: String, token: String, path: String): Any {
        val conn = (URL(origin + path).openConnection() as HttpURLConnection).apply {
            instanceFollowRedirects = false
            connectTimeout = 12_000
            readTimeout = 12_000
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("User-Agent", "MajaAndroid/1")
        }
        try {
            val code = conn.responseCode
            if (code !in 200..299) throw IllegalStateException("http $code")
            val text = conn.inputStream.bufferedReader().use { it.readText() }
            val trimmed = text.trim()
            return if (trimmed.startsWith("[")) JSONArray(trimmed) else JSONObject(trimmed)
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        private const val TAG = "Maja"
        private const val WORK = "maja-watch"
        private const val CHANNEL = "geofence"
        private const val NOTIFY_ID = 1001

        fun ensure(context: Context) {
            val wm = WorkManager.getInstance(context)
            if (Store.origin(context).isNullOrBlank() || Store.token(context).isNullOrBlank()) {
                wm.cancelUniqueWork(WORK)
                return
            }
            val req = PeriodicWorkRequestBuilder<WatchWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            wm.enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.UPDATE, req)
        }

        fun refreshNow(context: Context) {
            if (Store.token(context).isNullOrBlank()) {
                DogWidget.push(context)
                return
            }
            val req = OneTimeWorkRequestBuilder<WatchWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueue(req)
        }
    }
}
