package com.farryon.farryon

import com.farryon.farryon.glasses.GlassesChannels
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine

class MainActivity : FlutterActivity() {
    private var glasses: GlassesChannels? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // Glasses Lab bridge (debug test bench; stub SDK unless the vendor
        // .aar is wired in — see glasses/GlassesChannels.createSdk()).
        glasses = GlassesChannels.register(flutterEngine.dartExecutor.binaryMessenger)
    }

    override fun onDestroy() {
        glasses?.dispose()
        glasses = null
        super.onDestroy()
    }
}
