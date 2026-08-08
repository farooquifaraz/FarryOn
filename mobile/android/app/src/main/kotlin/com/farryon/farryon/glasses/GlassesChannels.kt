package com.farryon.farryon.glasses

import android.app.Application
import android.content.Context
import android.util.Log
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
                    Log.w("GlassesFactory", "vendor SDK class absent — stub mode", e)
                } catch (e: Throwable) {
                    // Vendor SDK present but failed to boot — stub mode.
                    Log.w("GlassesFactory", "vendor SDK boot failed — stub mode", e)
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
    /**
     * Option A: disconnect the glasses' classic-BT A2DP (audio) profile so the
     * assistant's TTS routes back to the phone speaker after a "disconnect
     * glasses" command. Android has no public A2DP-disconnect API, so we call
     * the hidden BluetoothA2dp.disconnect(device) via reflection (the standard
     * technique). We do NOT remove the bond — a later connect re-pairs cleanly.
     */
    private fun disconnectClassicAudio() {
        val ctx = appContext ?: return
        try {
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S &&
                ctx.checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT)
                != android.content.pm.PackageManager.PERMISSION_GRANTED
            ) {
                return
            }
            val mgr = ctx.getSystemService(android.content.Context.BLUETOOTH_SERVICE)
                as? android.bluetooth.BluetoothManager ?: return
            val adapter = mgr.adapter ?: return
            adapter.getProfileProxy(
                ctx,
                object : android.bluetooth.BluetoothProfile.ServiceListener {
                    override fun onServiceConnected(
                        profile: Int,
                        proxy: android.bluetooth.BluetoothProfile,
                    ) {
                        try {
                            val connected = proxy.connectedDevices
                            for (device in connected) {
                                proxy.javaClass
                                    .getMethod("disconnect", android.bluetooth.BluetoothDevice::class.java)
                                    .invoke(proxy, device)
                                android.util.Log.i("GlassesLab", "A2DP disconnect → ${device.address}")
                            }
                        } catch (e: Exception) {
                            android.util.Log.i("GlassesLab", "A2DP disconnect reflection failed: $e")
                        } finally {
                            adapter.closeProfileProxy(profile, proxy)
                        }
                    }

                    override fun onServiceDisconnected(profile: Int) {}
                },
                android.bluetooth.BluetoothProfile.A2DP,
            )
        } catch (e: Exception) {
            android.util.Log.i("GlassesLab", "disconnectClassicAudio failed: $e")
        }
    }

    /**
     * Connect the glasses' classic-BT A2DP (audio) profile so the assistant's
     * TTS plays THROUGH the glasses. The BLE link the SDK sets up only carries
     * control + mic PCM; media output is a separate A2DP connection that
     * Android does not always auto-restore (esp. after our Option-A disconnect).
     * No public connect API exists, so we call the hidden
     * BluetoothA2dp.connect(device) via reflection (mirror of the disconnect).
     */
    private fun connectClassicAudio(mac: String) {
        val ctx = appContext ?: return
        if (mac.isBlank()) return
        try {
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S &&
                ctx.checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT)
                != android.content.pm.PackageManager.PERMISSION_GRANTED
            ) {
                return
            }
            val mgr = ctx.getSystemService(android.content.Context.BLUETOOTH_SERVICE)
                as? android.bluetooth.BluetoothManager ?: return
            val adapter = mgr.adapter ?: return
            val device = try {
                adapter.getRemoteDevice(mac)
            } catch (e: Exception) {
                null
            } ?: return
            adapter.getProfileProxy(
                ctx,
                object : android.bluetooth.BluetoothProfile.ServiceListener {
                    override fun onServiceConnected(
                        profile: Int,
                        proxy: android.bluetooth.BluetoothProfile,
                    ) {
                        try {
                            if (proxy.getConnectionState(device) ==
                                android.bluetooth.BluetoothProfile.STATE_CONNECTED
                            ) {
                                android.util.Log.i("GlassesLab", "A2DP already connected → $mac")
                            } else {
                                proxy.javaClass
                                    .getMethod("connect", android.bluetooth.BluetoothDevice::class.java)
                                    .invoke(proxy, device)
                                android.util.Log.i("GlassesLab", "A2DP connect → $mac")
                            }
                        } catch (e: Exception) {
                            android.util.Log.i("GlassesLab", "A2DP connect reflection failed: $e")
                        } finally {
                            adapter.closeProfileProxy(profile, proxy)
                        }
                    }

                    override fun onServiceDisconnected(profile: Int) {}
                },
                android.bluetooth.BluetoothProfile.A2DP,
            )
        } catch (e: Exception) {
            android.util.Log.i("GlassesLab", "connectClassicAudio failed: $e")
        }
    }

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
            // Bring up A2DP audio only AFTER the BLE link is established — doing
            // both at once makes the classic + LE radios contend and slows the
            // BLE connect (measured: latency crept 4s→16s under rapid cycling).
            if (type == "connectionState" && (data["state"] as? String) == "connected") {
                val mac = (data["mac"] as? String).orEmpty()
                if (mac.isNotEmpty()) connectClassicAudio(mac)
            }
            eventSink?.success(mapOf("type" to type, "data" to data))
        }
        registerTestReceiver()
    }

    /**
     * Debug-only broadcast hook for automated stress testing over adb. Lets a
     * test harness drive the real bridge without the Flutter UI:
     *   adb shell am broadcast -a com.farryon.glasses.TEST --es cmd connect
     *   adb shell am broadcast -a com.farryon.glasses.TEST --es cmd disconnect
     *   adb shell am broadcast -a com.farryon.glasses.TEST --es cmd scan
     * `connect` uses the saved MAC + brings up A2DP, exactly like the app's
     * connect path. No-op in release builds.
     */
    private var testReceiver: android.content.BroadcastReceiver? = null

    private fun registerTestReceiver() {
        val ctx = appContext ?: return
        // Debug builds only — never wire a broadcast trigger into a release APK.
        if ((ctx.applicationInfo.flags and
                android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) == 0) {
            return
        }
        val rx = object : android.content.BroadcastReceiver() {
            override fun onReceive(c: Context?, intent: android.content.Intent?) {
                val cmd = intent?.getStringExtra("cmd") ?: return
                android.util.Log.i("GlassesLab", "TEST broadcast: $cmd")
                val prefs = ctx.getSharedPreferences("glasses_lab", Context.MODE_PRIVATE)
                val mac = prefs.getString("last_mac", null) ?: ""
                when (cmd) {
                    "connect" -> sdk.connect(mac) // A2DP follows on connected-event
                    "disconnect" -> {
                        sdk.disconnect()
                        disconnectClassicAudio()
                    }
                    "scan" -> sdk.scan(8000) { hits ->
                        android.util.Log.i("GlassesLab", "TEST scan → ${hits.size} hits")
                        for (h in hits) {
                            android.util.Log.i(
                                "GlassesLab",
                                "TEST hit: ${h["name"]} ${h["mac"]} ${h["rssi"]} dBm " +
                                    "bonded=${h["bonded"]} connected=${h["connected"]}"
                            )
                        }
                    }
                    // A PLAIN photo (not the AI/thumbnail variant): the only
                    // way to tell "the glasses stored nothing" apart from "the
                    // media-count probe is lying", which is exactly the
                    // question a missing recording raises.
                    "photo" -> sdk.takePhoto()
                    "video" -> sdk.startVideoRecording(
                        "test-${System.currentTimeMillis()}",
                        intent.getIntExtra("seconds", 30),
                    )
                    "stopvideo" -> sdk.stopVideoRecording()
                    // Force the WiFi transfer even when the count says empty,
                    // so the glasses' real album is what answers, not a probe.
                    "sync" -> sdk.forceWifiSync()
                    "rawtoggle" -> sdk.debugRawVideoToggle()
                    "info" -> sdk.requestDeviceInfo()
                    "resetp2p" -> sdk.debugResetP2p()
                    "vres" -> sdk.debugSetVideoResolution(
                        intent.getIntExtra("sel", 2),
                        intent.getIntExtra("value", 2),
                    )
                    // Connect to a SPECIFIC unit, so a second pair can be
                    // tested without driving the UI by hand.
                    "connectmac" -> {
                        val target = intent.getStringExtra("mac") ?: mac
                        android.util.Log.i("GlassesLab", "TEST connectmac → $target")
                        sdk.disconnect()
                        ctx.mainLooper?.let {
                            android.os.Handler(it).postDelayed(
                                { sdk.connect(target) }, 1500L
                            )
                        }
                    }
                }
            }
        }
        val filter = android.content.IntentFilter("com.farryon.glasses.TEST")
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            ctx.registerReceiver(rx, filter, Context.RECEIVER_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            ctx.registerReceiver(rx, filter)
        }
        testReceiver = rx
        android.util.Log.i("GlassesLab", "TEST receiver registered")
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
                    // A2DP audio is brought up in the connectionState=connected
                    // listener (init), AFTER the BLE link — so classic + LE
                    // don't contend and slow the connect.
                    sdk.connect(call.argument<String>("mac") ?: "")
                    result.success(null)
                }
                "disconnect" -> {
                    sdk.disconnect()
                    // Option A: also drop the glasses' classic-BT (A2DP)
                    // audio so TTS routes back to the phone speaker — the
                    // SDK's unBindDevice only kills the BLE link.
                    disconnectClassicAudio()
                    result.success(null)
                }
                "setAutoReconnect" -> {
                    sdk.setAutoReconnect(call.argument<Boolean>("enabled") ?: true)
                    result.success(null)
                }
                "setRetentionDays" -> {
                    sdk.setRetentionDays((call.argument<Number>("days") ?: 0).toInt())
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
                "startVideoRecording" -> {
                    // The recording outlives this call by up to four minutes,
                    // so the reply is just the correlation id; everything else
                    // arrives as `videoState` events.
                    val requestId = UUID.randomUUID().toString()
                    sdk.startVideoRecording(
                        requestId,
                        (call.argument<Number>("seconds") ?: 60).toInt(),
                    )
                    result.success(requestId)
                }
                "stopVideoRecording" -> { sdk.stopVideoRecording(); result.success(null) }
                "setVideoDuration" -> {
                    sdk.setVideoDuration((call.argument<Number>("seconds") ?: 60).toInt())
                    result.success(null)
                }
                "pairClassicBt" -> { sdk.pairClassicBt(); result.success(null) }
                "startAudioTest" -> {
                    sdk.startAudioTest(call.argument<String>("mode") ?: "hfp")
                    result.success(null)
                }
                "enableBluetooth" -> { enableBluetooth(); result.success(null) }
                "startMicService" -> {
                    appContext?.let { SessionMicService.start(it) }
                    result.success(null)
                }
                "stopMicService" -> {
                    appContext?.let { SessionMicService.stop(it) }
                    result.success(null)
                }
                "stopAudioTest" -> { sdk.stopAudioTest(); result.success(null) }
                "startWifiSync" -> { sdk.startWifiSync(); result.success(null) }
                "refreshMediaCounts" -> {
                    sdk.refreshMediaCounts(); result.success(null)
                }
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
        testReceiver?.let {
            try {
                appContext?.unregisterReceiver(it)
            } catch (e: Exception) {
                // Already unregistered — ignore.
            }
        }
        testReceiver = null
        sdk.dispose()
    }
}
