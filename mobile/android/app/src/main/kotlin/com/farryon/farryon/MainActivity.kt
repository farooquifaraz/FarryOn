package com.farryon.farryon

import com.farryon.farryon.glasses.GlassesChannels
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine

class MainActivity : FlutterActivity() {
    private var glasses: GlassesChannels? = null
    private var audioMode: AudioModeChannel? = null

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
        // Voice-call audio path during a live session (speakerphone only), so
        // the platform echo canceller has a real playback reference.
        audioMode = AudioModeChannel.register(
            flutterEngine.dartExecutor.binaryMessenger,
            applicationContext,
        )
    }

    override fun onDestroy() {
        glasses?.dispose()
        glasses = null
        // Never leave the phone stuck in call mode if we're torn down mid-session.
        audioMode?.exit()
        audioMode = null
        super.onDestroy()
    }
}
