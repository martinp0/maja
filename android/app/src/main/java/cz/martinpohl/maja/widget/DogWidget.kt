package cz.martinpohl.maja.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import cz.martinpohl.maja.MainActivity
import cz.martinpohl.maja.R
import cz.martinpohl.maja.Store
import cz.martinpohl.maja.WatchWorker

/** Malý widget na plochu a na kryt Z Flip: jméno, doma/venku, baterie. Bez souřadnic. */
class DogWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        push(context)
        WatchWorker.refreshNow(context)
    }

    override fun onEnabled(context: Context) {
        WatchWorker.ensure(context)
        WatchWorker.refreshNow(context)
    }

    companion object {
        fun push(context: Context) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, DogWidget::class.java))
            if (ids.isEmpty()) return
            val views = render(context)
            manager.updateAppWidget(ids, views)
        }

        private fun render(context: Context): RemoteViews {
            val views = RemoteViews(context.packageName, R.layout.widget_dog)
            val prefs = Store.prefs(context)
            val name = prefs.getString(Store.W_NAME, null)
            val where = prefs.getString(Store.W_WHERE, null)
            val battery = prefs.getString(Store.W_BATTERY, null)
            val seen = prefs.getString(Store.W_SEEN, null)
            val error = prefs.getBoolean(Store.W_ERROR, false)
            when {
                Store.origin(context).isNullOrBlank() -> {
                    views.setTextViewText(R.id.widget_name, context.getString(R.string.app_name))
                    views.setTextViewText(R.id.widget_where, context.getString(R.string.widget_need_server))
                    views.setTextViewText(R.id.widget_battery, "")
                    views.setTextViewText(R.id.widget_seen, "")
                }
                Store.token(context).isNullOrBlank() -> {
                    views.setTextViewText(R.id.widget_name, context.getString(R.string.app_name))
                    views.setTextViewText(R.id.widget_where, context.getString(R.string.widget_need_token))
                    views.setTextViewText(R.id.widget_battery, "")
                    views.setTextViewText(R.id.widget_seen, "")
                }
                error && where.isNullOrBlank() -> {
                    views.setTextViewText(R.id.widget_name, context.getString(R.string.app_name))
                    views.setTextViewText(R.id.widget_where, context.getString(R.string.widget_offline))
                    views.setTextViewText(R.id.widget_battery, "")
                    views.setTextViewText(R.id.widget_seen, "")
                }
                where.isNullOrBlank() -> {
                    views.setTextViewText(R.id.widget_name, context.getString(R.string.app_name))
                    views.setTextViewText(R.id.widget_where, context.getString(R.string.widget_loading))
                    views.setTextViewText(R.id.widget_battery, "")
                    views.setTextViewText(R.id.widget_seen, "")
                }
                else -> {
                    views.setTextViewText(R.id.widget_name, name ?: context.getString(R.string.app_name))
                    views.setTextViewText(R.id.widget_where, if (error) context.getString(R.string.widget_offline) else where)
                    views.setTextViewText(R.id.widget_battery, battery.orEmpty())
                    views.setTextViewText(R.id.widget_seen, seen.orEmpty())
                }
            }
            val pending = PendingIntent.getActivity(
                context,
                1,
                Intent(context, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            views.setOnClickPendingIntent(R.id.widget_root, pending)
            return views
        }
    }
}
