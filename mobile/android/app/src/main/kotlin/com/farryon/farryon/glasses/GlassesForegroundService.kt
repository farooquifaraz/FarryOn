package com.farryon.farryon.glasses

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder

/**
 * Keeps the process (and with it the BLE link to the glasses) alive while the
 * app is backgrounded — Task 2.6b. Started on the connected transition,
 * stopped on disconnect; START_STICKY so the system restarts it if killed.
 */
class GlassesForegroundService : Service() {

    companion object {
        private const val CHANNEL_ID = "glasses_link"
        private const val NOTIF_ID = 7801
        private const val EXTRA_DEVICE = "device"

        fun start(context: Context, deviceName: String) {
            val intent = Intent(context, GlassesForegroundService::class.java)
                .putExtra(EXTRA_DEVICE, deviceName)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, GlassesForegroundService::class.java))
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val mgr = getSystemService(NotificationManager::class.java)
            mgr.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "Glasses connection",
                    NotificationManager.IMPORTANCE_LOW,
                ).apply {
                    description = "Keeps the smart-glasses link alive"
                    setShowBadge(false)
                }
            )
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val device = intent?.getStringExtra(EXTRA_DEVICE) ?: "Smart glasses"
        val notification: Notification =
            (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                Notification.Builder(this, CHANNEL_ID)
            } else {
                @Suppress("DEPRECATION")
                Notification.Builder(this)
            })
                .setSmallIcon(applicationInfo.icon)
                .setContentTitle("FarryOn glasses connected")
                .setContentText("$device — link active")
                .setOngoing(true)
                .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIF_ID, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE,
            )
        } else {
            startForeground(NOTIF_ID, notification)
        }
        return START_STICKY
    }
}
