package com.farryon.farryon

import android.content.Intent
import com.farryon.farryon.glasses.GlassesChannels
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine

class MainActivity : FlutterActivity() {
    private var glasses: GlassesChannels? = null
    private var audioMode: AudioModeChannel? = null
    private var audioFocus: AudioFocusChannel? = null
    private var call: CallChannel? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // Glasses Lab bridge (debug test bench; real HeyCyan SDK when the
        // vendor .aar is present, stub otherwise — GlassesChannels.createSdk()).
        glasses = GlassesChannels.register(
            flutterEngine.dartExecutor.binaryMessenger,
            applicationContext,
        )
        // Save live captures (phone/glasses JPEG bytes) into the phone gallery.
        MediaChannel.register(
            flutterEngine.dartExecutor.binaryMessenger,
            applicationContext,
        )
        // "Call Ahmed" — placed for real, once the user has granted the
        // permission; falls back to the dialer when they haven't.
        call = CallChannel.register(
            flutterEngine.dartExecutor.binaryMessenger,
            applicationContext,
        )
        // "Play some music" — handed to whatever player the phone has.
        MusicChannel.register(
            flutterEngine.dartExecutor.binaryMessenger,
            applicationContext,
        )
        // Voice-call audio path during a live session (speakerphone only), so
        // the platform echo canceller has a real playback reference.
        audioMode = AudioModeChannel.register(
            flutterEngine.dartExecutor.binaryMessenger,
            applicationContext,
        )
        // Lower other apps' music while the user talks and Farry answers
        // (transient duck focus), and say whether music is playing at all.
        audioFocus = AudioFocusChannel.register(
            flutterEngine.dartExecutor.binaryMessenger,
            applicationContext,
        )
        // Translation is spoken by the phone's own voice, and a phone only has
        // the voices someone installed. This opens the screen where they can
        // add one — we never download tens of megabytes on their behalf.
        VoiceDataChannel.register(
            flutterEngine.dartExecutor.binaryMessenger,
            applicationContext,
        )
    }

    /**
     * A headset "voice assistant" press (the glasses' temple long-press)
     * arrives as ACTION_VOICE_COMMAND once the user has made Farry the
     * default handler. Warm start lands here; a cold start lands in
     * [onPostResume] via the launch intent. Either way the Dart side hears
     * `voiceCommand` and opens the mic if it is closed.
     */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleVoiceCommand(intent)
    }

    override fun onPostResume() {
        super.onPostResume()
        handleVoiceCommand(intent)
    }

    private fun handleVoiceCommand(intent: Intent?) {
        if (intent?.action != Intent.ACTION_VOICE_COMMAND) return
        // Consume it: a rotation or resume must not replay the press.
        intent.action = null
        glasses?.onVoiceCommand()
    }

    override fun onDestroy() {
        glasses?.dispose()
        glasses = null
        // Never leave the phone stuck in call mode if we're torn down mid-session.
        audioMode?.exit()
        audioMode = null
        audioFocus?.abandon()
        audioFocus = null
        call?.dispose()
        call = null
        super.onDestroy()
    }
}
