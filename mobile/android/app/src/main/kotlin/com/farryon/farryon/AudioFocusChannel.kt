package com.farryon.farryon

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import android.util.Log
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * Transient "may duck" audio focus for the moments a voice exchange is going
 * on, so a music player lowers itself while the user talks and Farry answers.
 *
 * The app held no audio focus at all until 2026-09-08; a player therefore had
 * no signal to step aside, and the user's command had to compete with the
 * music at full volume. This asks for the gentlest kind of focus there is:
 * transient, duck-allowed, on the ASSISTANT usage — the same request a
 * navigation prompt makes. The player decides what to do with it (lower its
 * volume, usually); nothing is paused by us, and [abandon] hands it back.
 *
 * Also answers [AudioManager.isMusicActive], so Dart can know whether anything
 * is playing on the music stream without guessing from tool calls.
 *
 * Never throws: focus must not be able to fail a session.
 */
class AudioFocusChannel(private val app: Context) : MethodChannel.MethodCallHandler {
    companion object {
        private const val TAG = "AudioFocusChannel"
        private const val CHANNEL = "com.farryon/audio_focus"

        fun register(messenger: BinaryMessenger, app: Context): AudioFocusChannel {
            val handler = AudioFocusChannel(app)
            MethodChannel(messenger, CHANNEL).setMethodCallHandler(handler)
            return handler
        }
    }

    private val audio: AudioManager?
        get() = app.getSystemService(Context.AUDIO_SERVICE) as? AudioManager

    private var request: AudioFocusRequest? = null
    private var held = false

    private val listener = AudioManager.OnAudioFocusChangeListener { change ->
        // We only ever hold focus briefly; if someone takes it from us there is
        // nothing to pause on our side. Log it so a device trace explains
        // itself.
        Log.i(TAG, "focus change: $change")
        if (change == AudioManager.AUDIOFOCUS_LOSS) held = false
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            // "granted" | "denied" | "unavailable"
            "requestDuck" -> result.success(requestDuck())
            "abandon" -> {
                abandon()
                result.success(null)
            }
            "isMusicActive" -> result.success(audio?.isMusicActive ?: false)
            else -> result.notImplemented()
        }
    }

    private fun requestDuck(): String {
        val am = audio ?: return "unavailable"
        if (held) return "granted"
        return try {
            val granted = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
                    .setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_ASSISTANT)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build(),
                    )
                    .setWillPauseWhenDucked(false)
                    .setOnAudioFocusChangeListener(listener)
                    .build()
                request = req
                am.requestAudioFocus(req)
            } else {
                @Suppress("DEPRECATION")
                am.requestAudioFocus(
                    listener,
                    AudioManager.STREAM_MUSIC,
                    AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK,
                )
            }
            held = granted == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
            Log.i(TAG, "requestDuck: " + (if (held) "granted" else "denied ($granted)"))
            if (held) "granted" else "denied"
        } catch (e: Throwable) {
            Log.i(TAG, "requestDuck failed: $e")
            held = false
            "unavailable"
        }
    }

    fun abandon() {
        val am = audio ?: return
        if (!held && request == null) return
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                request?.let { am.abandonAudioFocusRequest(it) }
            } else {
                @Suppress("DEPRECATION")
                am.abandonAudioFocus(listener)
            }
            Log.i(TAG, "abandon: focus released")
        } catch (e: Throwable) {
            Log.i(TAG, "abandon failed: $e")
        } finally {
            held = false
            request = null
        }
    }
}
