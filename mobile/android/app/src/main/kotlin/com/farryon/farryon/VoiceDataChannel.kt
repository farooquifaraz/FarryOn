package com.farryon.farryon

import android.content.Context
import android.content.Intent
import android.speech.tts.TextToSpeech
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * Opening Android's voice-data installer.
 *
 * Live translation now speaks with the phone's own text-to-speech rather than
 * buying generated audio — about 80% of what a minute of translation used to
 * cost. The catch is that a phone only has voices for the languages someone
 * installed: pick a target the engine cannot say and the translation appears on
 * screen and is never spoken.
 *
 * Flutter's TTS plugin can tell us which languages are installed but offers no
 * way to install one, so this does the one thing it cannot: hand the user over
 * to the screen where they can fix it. Nothing is downloaded on their behalf —
 * voice data is tens of megabytes and that is their call, not ours.
 */
class VoiceDataChannel(private val context: Context) : MethodChannel.MethodCallHandler {

    companion object {
        private const val CHANNEL = "com.farryon/voice_data"

        fun register(messenger: BinaryMessenger, context: Context): VoiceDataChannel {
            val handler = VoiceDataChannel(context)
            MethodChannel(messenger, CHANNEL).setMethodCallHandler(handler)
            return handler
        }
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            // The engine's own "install voice data" screen. Preferred, because
            // it lands exactly where the languages are.
            "installVoices" -> result.success(open(Intent(TextToSpeech.Engine.ACTION_INSTALL_TTS_DATA)))
            // Where speech settings live, for engines that do not offer the
            // installer above. Reached from the same button as a fallback.
            "openSettings" -> result.success(open(Intent("com.android.settings.TTS_SETTINGS")))
            else -> result.notImplemented()
        }
    }

    /** True if the screen actually opened. Some phones have neither. */
    private fun open(intent: Intent): Boolean = try {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        true
    } catch (e: Exception) {
        // No such screen on this device. The caller tells the user where to go
        // by hand rather than pretending a button worked.
        false
    }
}
