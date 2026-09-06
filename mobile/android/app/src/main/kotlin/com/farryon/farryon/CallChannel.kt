package com.farryon.farryon

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * Places a call outright, instead of handing the user a filled-in dialer.
 *
 * This is the one thing in the app that acts on the world without a further
 * human press, so the guard rails live in the layers around it: the assistant
 * must confirm the recipient out loud first, and a contact spoken by name is
 * resolved on the device and read back as a masked number before anything
 * happens here.
 *
 * [CALL_PHONE][Manifest.permission.CALL_PHONE] is a runtime permission the user
 * can refuse, so this NEVER pretends: with no permission it returns
 * `"no_permission"` and the Dart side falls back to opening the dialer, which
 * always works. The caller can then say what actually happened.
 */
class CallChannel(private val app: Context) : MethodChannel.MethodCallHandler {
    companion object {
        private const val TAG = "CallChannel"
        private const val CHANNEL = "com.farryon/call"

        fun register(messenger: BinaryMessenger, app: Context): CallChannel {
            val handler = CallChannel(app)
            val channel = MethodChannel(messenger, CHANNEL)
            channel.setMethodCallHandler(handler)
            handler.watchAudioMode(channel)
            return handler
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private var modeListener: AudioManager.OnModeChangedListener? = null
    private var audio: AudioManager? = null
    private var inCall = false

    /**
     * Tell Dart when a phone call takes over the audio, and when it gives it back.
     *
     * Device-proven 2026-09-06: while a call was up, FarryOn still held the
     * microphone (`src:VOICE_COMMUNICATION`) and owned the audio mode
     * (`MODE_IN_COMMUNICATION`), so the person on the other end heard nothing.
     * A live session and a phone call cannot share the one microphone — the
     * call must win, and the session has to step aside and come back after.
     *
     * The listener is API 31+. Below that the app keeps the old behaviour
     * rather than polling the mode forever; every device we ship to today is
     * well above it.
     */
    fun watchAudioMode(channel: MethodChannel) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        val am = app.getSystemService(Context.AUDIO_SERVICE) as? AudioManager ?: return
        audio = am
        val listener = AudioManager.OnModeChangedListener { mode ->
            // MODE_IN_CALL is a telephony call; MODE_RINGTONE is one arriving.
            // Anything else means the phone is ours again.
            val busy = mode == AudioManager.MODE_IN_CALL ||
                mode == AudioManager.MODE_RINGTONE
            if (busy == inCall) return@OnModeChangedListener
            inCall = busy
            Log.i(TAG, "phone call " + if (busy) "started" else "ended")
            main.post { channel.invokeMethod("callAudio", mapOf("inCall" to busy)) }
        }
        modeListener = listener
        am.addOnModeChangedListener(app.mainExecutor, listener)
    }

    /** Drop the listener with the engine, so a torn-down activity leaks nothing. */
    fun dispose() {
        val listener = modeListener ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audio?.removeOnModeChangedListener(listener)
        }
        modeListener = null
        audio = null
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "placeCall" -> {
                val number = call.argument<String>("number").orEmpty()
                if (number.isBlank()) {
                    result.error("no_number", "a number is required", null)
                    return
                }
                result.success(placeCall(number))
            }
            "canPlaceCall" -> result.success(hasPermission())
            "isInCall" -> result.success(inCall)
            else -> result.notImplemented()
        }
    }

    private fun hasPermission(): Boolean =
        ContextCompat.checkSelfPermission(app, Manifest.permission.CALL_PHONE) ==
            PackageManager.PERMISSION_GRANTED

    /** "called", "no_permission", or "failed" — never a silent success. */
    private fun placeCall(number: String): String {
        if (!hasPermission()) return "no_permission"
        val intent = Intent(Intent.ACTION_CALL, Uri.parse("tel:$number")).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return try {
            app.startActivity(intent)
            "called"
        } catch (e: SecurityException) {
            // The permission can be revoked between the check and the call.
            Log.w(TAG, "call refused by the platform", e)
            "no_permission"
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "no app can place calls on this device", e)
            "failed"
        }
    }
}
