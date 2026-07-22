package com.farryon.farryon.glasses

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager

/**
 * Keeps the live session alive while the phone screen is OFF.
 *
 * Android 11+ blocks microphone capture from the background unless a foreground
 * service of type `microphone` is running — without this, turning the screen
 * off muted the mic and the user could no longer talk to Farry. A partial
 * wake-lock also keeps the CPU (and with it the audio pump + WebSocket) running
 * through Doze so the reply keeps streaming.
 *
 * Started on session connect, stopped on disconnect. START_STICKY so the system
 * brings it back if it's killed mid-session.
 */
class SessionMicService : Service() {

    companion object {
        private const val CHANNEL_ID = "farry_session"
        private const val NOTIF_ID = 7802

        fun start(context: Context) {
            // A microphone-type FGS is illegal without RECORD_AUDIO (Android 14+
            // throws in startForeground). If the permission was revoked, don't
            // start the service at all — the session runs in mic-denied mode.
            if (!hasMicPermission(context)) return
            val intent = Intent(context, SessionMicService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        private fun hasMicPermission(context: Context): Boolean =
            context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED

        fun stop(context: Context) {
            context.stopService(Intent(context, SessionMicService::class.java))
        }
    }

    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val mgr = getSystemService(NotificationManager::class.java)
            mgr.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "Farry live session",
                    NotificationManager.IMPORTANCE_LOW,
                ).apply {
                    description = "Keeps the mic active so you can talk with the screen off"
                    setShowBadge(false)
                }
            )
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // RECORD_AUDIO can be revoked while the service is sticky-restarting
        // (user toggles it off mid-session); startForeground with the
        // microphone type then throws SecurityException and crashes the app.
        // Bail out instead — stopSelf() satisfies the startForegroundService
        // contract, and NOT_STICKY stops the system from restarting us into
        // the same crash.
        if (!hasMicPermission(this)) {
            stopSelf()
            return START_NOT_STICKY
        }
        val notification: Notification =
            (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                Notification.Builder(this, CHANNEL_ID)
            } else {
                @Suppress("DEPRECATION")
                Notification.Builder(this)
            })
                .setSmallIcon(applicationInfo.icon)
                .setContentTitle("Farry is listening")
                .setContentText("Tap to return — talk any time, even with the screen off")
                .setOngoing(true)
                .build()
        // Android 10+ must declare the FGS type; microphone is the one that
        // keeps background mic capture legal.
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                    NOTIF_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
                )
            } else {
                startForeground(NOTIF_ID, notification)
            }
        } catch (e: Exception) {
            // Permission state can change between the check above and here
            // (or the OS can refuse the FGS for other policy reasons, e.g.
            // ForegroundServiceStartNotAllowedException). A dead mic service
            // must never take the whole app down.
            stopSelf()
            return START_NOT_STICKY
        }
        acquireWakeLock()
        return START_STICKY
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK, "farry:session",
            ).apply { setReferenceCounted(false); acquire(60 * 60 * 1000L) }
        } catch (e: Exception) {
            // Wake-lock is best-effort; the FGS alone still keeps the mic legal.
        }
    }

    override fun onDestroy() {
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (e: Exception) {
            // Already released — ignore.
        }
        wakeLock = null
        super.onDestroy()
    }
}
