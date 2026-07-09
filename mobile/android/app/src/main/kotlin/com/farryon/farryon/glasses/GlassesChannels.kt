package com.farryon.farryon.glasses

import android.app.Application
import android.content.Context
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.util.UUID

/**
 * Registers the two platform channels behind the Glasses Lab:
 *
 *  - MethodChannel  `com.farryon/glasses`         — commands (Dart → native)
 *  - EventChannel   `com.farryon/glasses/events`  — device data (native → Dart)
 *
 * The channels talk only to the [GlassesSdk] interface. Today that resolves to
 * [StubGlassesSdk]; when the vendor .aar is present, swap the factory in
 * [createSdk] to the real `HeyCyanGlassesSdk` (Sprint 2) — no channel or Dart
 * changes needed.
 */
class GlassesChannels private constructor(
    private val sdk: GlassesSdk,
    private val appContext: Context?,
) : MethodChannel.MethodCallHandler, EventChannel.StreamHandler {

    companion object {
        /**
         * [appContext] unlocks the real SDK (it needs an Application for BLE
         * setup); the old context-less call keeps compiling and yields the
         * stub, so nothing outside this folder is forced to change.
         */
        fun register(
            messenger: BinaryMessenger,
            appContext: Context? = null,
        ): GlassesChannels {
            val channels = GlassesChannels(createSdk(appContext), appContext)
            MethodChannel(messenger, "com.farryon/glasses")
                .setMethodCallHandler(channels)
            EventChannel(messenger, "com.farryon/glasses/events")
                .setStreamHandler(channels)
            return channels
        }

        /**
         * SDK selection: HeyCyan when the vendor .aar is on the classpath
         * (dev machines with `app/libs/` populated) and a context is
         * available; the stub otherwise, so emulators and machines without
         * the .aar keep working. Any vendor init failure also falls back —
         * the Lab must never break the app.
         */
        private fun createSdk(appContext: Context?): GlassesSdk {
            val app = appContext?.applicationContext as? Application
            if (app != null) {
                try {
                    Class.forName("com.oudmon.ble.base.bluetooth.BleOperateManager")
                    return HeyCyanGlassesSdk(app)
                } catch (e: ClassNotFoundException) {
                    // No vendor .aar in this build — stub mode.
                } catch (e: Throwable) {
                    // Vendor SDK present but failed to boot — stub mode.
                }
            }
            return StubGlassesSdk()
        }
    }

    private var eventSink: EventChannel.EventSink? = null

    /**
     * Fire Android's system "turn on Bluetooth?" prompt (voice tool
     * `enable_bluetooth`). Android 13+ forbids silently enabling BT, so this
     * is the most an app can do — the user taps Allow. SDK-independent, so it
     * works in stub mode too.
     */
    private fun enableBluetooth() {
        val ctx = appContext ?: return
        try {
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S &&
                ctx.checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT)
                != android.content.pm.PackageManager.PERMISSION_GRANTED
            ) {
                android.util.Log.i("GlassesLab", "enableBluetooth: BLUETOOTH_CONNECT not granted")
            }
            ctx.startActivity(
                android.content.Intent(android.bluetooth.BluetoothAdapter.ACTION_REQUEST_ENABLE)
                    .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: Exception) {
            android.util.Log.i("GlassesLab", "enableBluetooth failed: $e")
        }
    }

    init {
        sdk.setListener { type, data ->
            // Already on the main thread (GlassesSdkListener contract).
            eventSink?.success(mapOf("type" to type, "data" to data))
        }
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        android.util.Log.i("GlassesLab", "cmd ${call.method} ${call.arguments ?: ""}")
        try {
            when (call.method) {
                "bridgeInfo" -> {
                    // Last-connected device → the Lab can offer instant
                    // Connect without a scan.
                    val prefs = appContext?.getSharedPreferences(
                        "glasses_lab", Context.MODE_PRIVATE
                    )
                    result.success(
                        mapOf(
                            "implementation" to sdk.implementationName,
                            "sdkVersion" to sdk.sdkVersion,
                            "lastMac" to prefs?.getString("last_mac", null),
                            "lastName" to prefs?.getString("last_name", null),
                        )
                    )
                }
                "scan" -> {
                    val timeoutMs = (call.argument<Number>("timeoutMs") ?: 8000).toLong()
                    sdk.scan(timeoutMs) { hits -> result.success(hits) }
                }
                "connect" -> {
                    sdk.connect(call.argument<String>("mac") ?: "")
                    result.success(null)
                }
                "disconnect" -> { sdk.disconnect(); result.success(null) }
                "setAutoReconnect" -> {
                    sdk.setAutoReconnect(call.argument<Boolean>("enabled") ?: true)
                    result.success(null)
                }
                "requestBattery" -> { sdk.requestBattery(); result.success(null) }
                "requestDeviceInfo" -> { sdk.requestDeviceInfo(); result.success(null) }
                "takePhoto" -> { sdk.takePhoto(); result.success(null) }
                "takeAiPhoto" -> {
                    val requestId = UUID.randomUUID().toString()
                    sdk.takeAiPhoto(requestId)
                    result.success(requestId)
                }
                "pairClassicBt" -> { sdk.pairClassicBt(); result.success(null) }
                "startAudioTest" -> {
                    sdk.startAudioTest(call.argument<String>("mode") ?: "hfp")
                    result.success(null)
                }
                "enableBluetooth" -> { enableBluetooth(); result.success(null) }
                "stopAudioTest" -> { sdk.stopAudioTest(); result.success(null) }
                "startWifiSync" -> { sdk.startWifiSync(); result.success(null) }
                "stopWifiSync" -> { sdk.stopWifiSync(); result.success(null) }
                "setVolume" -> {
                    sdk.setVolume(
                        call.argument<String>("type") ?: "system",
                        (call.argument<Number>("level") ?: 50).toInt(),
                    )
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        } catch (e: Exception) {
            result.error("glasses_error", e.message, null)
        }
    }

    override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
        eventSink = events
    }

    override fun onCancel(arguments: Any?) {
        eventSink = null
    }

    fun dispose() {
        eventSink = null
        sdk.dispose()
    }
}
