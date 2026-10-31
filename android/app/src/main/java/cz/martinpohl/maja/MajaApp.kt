package cz.martinpohl.maja

import android.app.Application

class MajaApp : Application() {
    override fun onCreate() {
        super.onCreate()
        WatchWorker.ensure(this)
    }
}
