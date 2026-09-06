package com.farryon.farryon

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.util.Log
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * Puts the phone on its VOICE-CALL audio path for the duration of a live
 * session, so the platform's echo canceller actually works.
 *
 * Why. We already record from [android.media.MediaRecorder.AudioSource.VOICE_COMMUNICATION],
 * and `dumpsys audio` confirms an "Acoustic Echo Canceler" effect is attached
 * to the capture. But the assistant's voice plays out on the MEDIA path
 * (`USAGE_MEDIA`) while the device sits in `MODE_NORMAL`, so the canceller has
 * no dependable reference for what the speaker is emitting — it can only guess,
 * and residual echo leaks back in as fake "user" turns (device-proven
 * 2026-08-05: the chat filled with garbled versions of Farry's own sentences).
 * `MODE_IN_COMMUNICATION` puts capture and playback on the same voice path,
 * which is what the platform AEC is designed around — the same thing every
 * call app (and ChatGPT's voice mode) does.
 *
 * What it deliberately does NOT do. If audio is going to a Bluetooth device —
 * the L80x glasses, earbuds — this is a NO-OP. Communication mode drags
 * Bluetooth audio from A2DP down to the narrowband SCO/HFP profile, which
 * would wreck the glasses' sound quality (and on some phones silently reroute
 * it to the handset speaker). Those routes also have far less echo to begin
 * with: the speaker sits at the ear, not next to the microphone. Same for a
 * wired headset. The speakerphone case is where the echo problem lives, and
 * the only case this touches.
 */
class AudioModeChannel(private val app: Context) : MethodChannel.MethodCallHandler {
    companion object {
        private const val TAG = "AudioModeChannel"
        private const val CHANNEL = "com.farryon/audio_mode"

        fun register(messenger: BinaryMessenger, app: Context): AudioModeChannel {
            val handler = AudioModeChannel(app)
            MethodChannel(messenger, CHANNEL).setMethodCallHandler(handler)
            return handler
        }
    }

    private val audio: AudioManager?
        get() = app.getSystemService(Context.AUDIO_SERVICE) as? AudioManager

    /** Whether WE switched the mode, so we only ever restore our own change. */
    private var applied = false

    /** Mode the device was in before we touched it. */
    private var previousMode = AudioManager.MODE_NORMAL

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            // Returns "applied" | "skipped_external_route" | "unavailable".
            // Never throws: audio routing must not be able to fail a session.
            "enterVoiceMode" -> result.success(enter())
            "exitVoiceMode" -> {
                exit()
                result.success(null)
            }
            else -> result.notImplemented()
        }
    }

    private fun enter(): String {
        val am = audio ?: return "unavailable"
        if (applied) return "applied"
        if (hasExternalAudioRoute(am)) {
            Log.i(TAG, "enterVoiceMode: external audio route — leaving media path alone")
            return "skipped_external_route"
        }
        // A phone call owns the audio while it lasts. Taking the mode from it
        // was device-proven to leave the person on the other end hearing
        // nothing (2026-09-06), so the call wins and we stay out.
        if (am.mode == AudioManager.MODE_IN_CALL || am.mode == AudioManager.MODE_RINGTONE) {
            Log.i(TAG, "enterVoiceMode: a phone call owns the audio — standing down")
            return "skipped_in_call"
        }
        return try {
            previousMode = am.mode
            am.mode = AudioManager.MODE_IN_COMMUNICATION
            // Communication mode defaults to the earpiece; the whole point here
            // is the hands-free speaker, so force it.
            @Suppress("DEPRECATION")
            am.isSpeakerphoneOn = true
            applied = true
            Log.i(TAG, "enterVoiceMode: MODE_IN_COMMUNICATION + speakerphone (was $previousMode)")
            "applied"
        } catch (e: Throwable) {
            Log.i(TAG, "enterVoiceMode failed: $e")
            applied = false
            "unavailable"
        }
    }

    fun exit() {
        val am = audio ?: return
        if (!applied) return
        try {
            @Suppress("DEPRECATION")
            am.isSpeakerphoneOn = false
            am.mode = previousMode
            Log.i(TAG, "exitVoiceMode: restored mode $previousMode")
        } catch (e: Throwable) {
            Log.i(TAG, "exitVoiceMode failed: $e")
        } finally {
            applied = false
        }
    }

    /** True when audio is (or is about to be) playing somewhere other than the
     *  phone's own loudspeaker: Bluetooth (glasses/earbuds) or a wired headset. */
    private fun hasExternalAudioRoute(am: AudioManager): Boolean = try {
        am.getDevices(AudioManager.GET_DEVICES_OUTPUTS).any {
            when (it.type) {
                AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
                AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
                AudioDeviceInfo.TYPE_WIRED_HEADSET,
                AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
                AudioDeviceInfo.TYPE_USB_HEADSET,
                -> true
                else -> false
            }
        }
    } catch (e: Throwable) {
        // On any doubt, assume there IS an external route and change nothing.
        Log.i(TAG, "route check failed, staying on the media path: $e")
        true
    }
}
