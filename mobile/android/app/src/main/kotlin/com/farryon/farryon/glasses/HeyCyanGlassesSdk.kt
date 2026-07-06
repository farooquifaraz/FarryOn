package com.farryon.farryon.glasses

import android.app.Application
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.util.Log
import com.oudmon.wifi.GlassesControl
import java.io.File
import java.io.FileOutputStream
import java.util.Locale
import com.oudmon.ble.base.bluetooth.BleAction
import com.oudmon.ble.base.bluetooth.BleBaseControl
import com.oudmon.ble.base.bluetooth.BleOperateManager
import com.oudmon.ble.base.bluetooth.DeviceManager
import com.oudmon.ble.base.bluetooth.QCBluetoothCallbackCloneReceiver
import com.oudmon.ble.base.communication.LargeDataHandler
import com.oudmon.ble.base.communication.bigData.resp.GlassesDeviceNotifyListener
import com.oudmon.ble.base.communication.bigData.resp.GlassesDeviceNotifyRsp
import com.oudmon.ble.base.scan.BleScannerHelper
import com.oudmon.ble.base.scan.ScanRecord
import com.oudmon.ble.base.scan.ScanWrapperCallback

/**
 * Real L801 bridge over the HeyCyan Android SDK (LIB_GLASSES_SDK-release_3.aar,
 * v1.0.2 2025-08-16). Task 2.3 scope: connection + device events only —
 * camera (2.4), audio (2.5) and WiFi sync (2.6) still answer with a
 * `deviceEvent` marker so the console shows the tap was received.
 *
 * Ground truth is the vendor GlassesSDKSample (MyApplication + MainActivity +
 * DeviceBindActivity + MyBluetoothReceiver), not the machine-translated PDF.
 * Every SDK callback is marshalled onto the main thread before it reaches
 * [GlassesSdkListener] (BLE callbacks arrive on binder threads).
 *
 * Only [GlassesChannels.createSdk] constructs this class, behind a
 * `Class.forName` guard, so machines without the .aar keep the stub.
 */
class HeyCyanGlassesSdk(private val app: Application) : GlassesSdk {
    companion object {
        /** `adb logcat -s GlassesLab` follows the whole bridge remotely. */
        const val TAG = "GlassesLab"
    }

    override val implementationName = "heycyan"
    override val sdkVersion = "1.0.2"

    private val main = Handler(Looper.getMainLooper())
    private var listener: GlassesSdkListener? = null

    /** MAC handed to the latest connect(); used for the connected event. */
    private var pendingMac: String? = null
    private var receiverRegistered = false

    /**
     * Last connectionState forwarded to Dart. The SDK re-broadcasts
     * service-discovered every ~2.5 s on a live link (measured on the L801,
     * 2026-07-05), so transitions are deduped for the Lab console while the
     * raw callbacks stay visible in logcat.
     */
    private var lastConnectionState: String? = null

    /**
     * True between a user-initiated disconnect and the next connect(). The
     * SDK's periodic service-discovered re-broadcast can land AFTER
     * unBindDevice (34 ms after, measured 2026-07-05) and would otherwise
     * resurrect a phantom "connected" in the Lab.
     */
    private var userDisconnected = false

    /** Lab toggle state, applied to the SDK on every fresh link (guide §3). */
    private var autoReconnectEnabled = true

    /** In-flight AI photo: request id + t0 for the capture→thumbnail latency. */
    private var photoRequestId: String? = null
    private var photoStartMs: Long = 0

    // -- Audio test state (Task 2.5) ------------------------------------------
    /** null | hfp | pcm | tts — which lab audio mode is armed. */
    @Volatile private var audioMode: String? = null

    /** Glasses-mic PCM measurement (voiceFromGlasses). */
    private var pcmBytesTotal = 0L
    private var pcmChunks = 0
    private var pcmStartMs = 0L
    private var pcmOut: FileOutputStream? = null

    @Volatile private var hfpRecording = false
    private var tts: TextToSpeech? = null
    private var classicBtReceiverRegistered = false
    private var lastWifiSpeedKbps = 0.0

    /** Pending "still connecting?" check — cleared on connect/disconnect. */
    private var connectWatchdog: Runnable? = null

    private fun bluetoothEnabled(): Boolean =
        (app.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)
            ?.adapter?.isEnabled == true

    private fun requestEnableBluetooth() {
        try {
            app.startActivity(
                Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: Exception) {
            // BLUETOOTH_CONNECT revoked or no dialog available — the console
            // message already tells the user what to do.
            Log.i(TAG, "enable-BT dialog failed: $e")
        }
    }

    private fun armConnectWatchdog() {
        cancelConnectWatchdog()
        val r = Runnable {
            if (lastConnectionState != "connected") {
                emit(
                    "deviceEvent",
                    mapOf("hex" to "connect timeout (20 s) — glasses off / out of range?")
                )
                emitConnectionState("disconnected")
            }
        }
        connectWatchdog = r
        main.postDelayed(r, 20_000L)
    }

    private fun cancelConnectWatchdog() {
        connectWatchdog?.let(main::removeCallbacks)
        connectWatchdog = null
    }

    /**
     * Phone-Bluetooth toggles mid-session (hit on-device 2026-07-06: user
     * turned BT off right after Connect → UI hung on "connecting" silently).
     * Sample/guide mapping: OFF → setBluetoothTurnOff(false); ON →
     * setBluetoothTurnOff(true) + connectDirectly for auto-reattach.
     */
    private val btStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action != BluetoothAdapter.ACTION_STATE_CHANGED) return
            when (intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, -1)) {
                BluetoothAdapter.STATE_OFF -> {
                    emit("deviceEvent", mapOf("hex" to "phone Bluetooth turned OFF"))
                    cancelConnectWatchdog()
                    try {
                        BleOperateManager.getInstance().setBluetoothTurnOff(false)
                    } catch (e: Exception) {
                        Log.i(TAG, "setBluetoothTurnOff(false): $e")
                    }
                    emitConnectionState("disconnected")
                }
                BluetoothAdapter.STATE_ON -> {
                    emit("deviceEvent", mapOf("hex" to "phone Bluetooth back ON"))
                    try {
                        BleOperateManager.getInstance().setBluetoothTurnOff(true)
                    } catch (e: Exception) {
                        Log.i(TAG, "setBluetoothTurnOff(true): $e")
                    }
                    val mac = pendingMac
                    if (autoReconnectEnabled && !userDisconnected && mac != null) {
                        emit("deviceEvent", mapOf("hex" to "auto-reconnecting to $mac"))
                        BleOperateManager.getInstance().connectDirectly(mac)
                        armConnectWatchdog()
                    }
                }
            }
        }
    }

    /** Same folder the vendor sample uses ('DCIM_1') — synced media + PCM. */
    private val albumDir: File by lazy {
        File(app.getExternalFilesDir(null), "DCIM_1").apply { mkdirs() }
    }

    private fun emit(type: String, data: Map<String, Any?>) {
        Log.i(TAG, "event $type $data")
        main.post { listener?.onEvent(type, data) }
    }

    private fun emitConnectionState(state: String, mac: String? = null) {
        if (state == lastConnectionState) {
            Log.i(TAG, "connectionState $state (repeat, not forwarded)")
            return
        }
        lastConnectionState = state
        emit(
            "connectionState",
            if (mac != null) mapOf("state" to state, "mac" to mac)
            else mapOf("state" to state),
        )
        // Task 2.6b: the BLE link must survive backgrounding while connected.
        try {
            when (state) {
                "connected" -> GlassesForegroundService.start(
                    app, DeviceManager.getInstance().deviceName ?: "L801"
                )
                "disconnected" -> GlassesForegroundService.stop(app)
            }
        } catch (e: Exception) {
            Log.i(TAG, "foreground service: $e")
        }
    }

    /**
     * The vendor SDK publishes GATT events through
     * androidx.localbroadcastmanager, which is on this app's runtime
     * classpath only transitively (another plugin ships it) — it is not a
     * declared dependency, so it cannot be referenced at compile time
     * without a gradle change outside the allowed folders. Reflection keeps
     * the wiring inside this module; if the class ever disappears the
     * throw lands in [GlassesChannels.createSdk]'s guard → stub fallback.
     */
    private fun localBroadcast(
        register: Boolean,
        receiver: BroadcastReceiver,
        filter: IntentFilter? = null,
    ) {
        val cls =
            Class.forName("androidx.localbroadcastmanager.content.LocalBroadcastManager")
        val mgr = cls.getMethod("getInstance", Context::class.java).invoke(null, app)
        if (register) {
            cls.getMethod(
                "registerReceiver",
                BroadcastReceiver::class.java,
                IntentFilter::class.java,
            ).invoke(mgr, receiver, filter)
        } else {
            cls.getMethod("unregisterReceiver", BroadcastReceiver::class.java)
                .invoke(mgr, receiver)
        }
    }

    override fun setListener(listener: GlassesSdkListener?) {
        this.listener = listener
    }

    // -- Connection ------------------------------------------------------------

    /**
     * GATT link + service-discovery events. The sample treats
     * onServiceDiscovered (not connectStatue(true)) as "ready for commands",
     * so `connected` is only emitted there.
     */
    private val bleReceiver = object : QCBluetoothCallbackCloneReceiver() {
        override fun connectStatue(device: BluetoothDevice?, connected: Boolean) {
            if (connected) {
                val name = try {
                    device?.name
                } catch (e: SecurityException) {
                    null // BLUETOOTH_CONNECT revoked mid-session
                }
                if (name != null) DeviceManager.getInstance().deviceName = name
                // Link is up but services aren't — wait for onServiceDiscovered.
            } else {
                emitConnectionState("disconnected")
            }
        }

        override fun onServiceDiscovered() {
            cancelConnectWatchdog()
            if (userDisconnected) {
                // Stale broadcast (or the link survived unBind) after the
                // user chose disconnect — kill it again, emit nothing.
                Log.i(TAG, "service-discovered after user disconnect — re-unbinding")
                BleOperateManager.getInstance().unBindDevice()
                return
            }
            LargeDataHandler.getInstance().initEnable()
            BleOperateManager.getInstance().isReady = true
            val wasConnected = lastConnectionState == "connected"
            emitConnectionState(
                "connected",
                pendingMac ?: DeviceManager.getInstance().deviceAddress,
            )
            // Post-connect sequence per the integration guide (§2.3/§3):
            // listener + time sync + auto-reconnect only AFTER services are
            // discovered — once per transition, not on every re-broadcast.
            if (!wasConnected) {
                LargeDataHandler.getInstance()
                    .addOutDeviceListener(100, notifyListener)
                LargeDataHandler.getInstance().syncTime { _, _ -> }
                BleOperateManager.getInstance().setNeedConnect(autoReconnectEnabled)
                requestBattery()
                // Wear reporting is OFF by default (verified 2026-07-05: wear
                // on/off emitted nothing) — enable it on every fresh link.
                // First session: wearCheck's callback never fired — query
                // whether this unit supports wear detection at all.
                LargeDataHandler.getInstance().wearFunctionSupport { _, rsp ->
                    emit(
                        "deviceEvent",
                        mapOf(
                            "hex" to "support: wear=${rsp?.isWearCheckSupport} " +
                                "volume=${rsp?.isVolumeControl} " +
                                "translation=${rsp?.isTranslationSupport} " +
                                "model=${rsp?.glassesModel}"
                        )
                    )
                }
                LargeDataHandler.getInstance().wearCheck(true, true) { _, rsp ->
                    emit(
                        "deviceEvent",
                        mapOf("hex" to "wearCheck enabled, open=${rsp?.isOpen}")
                    )
                }
            }
        }
    }

    override fun scan(timeoutMs: Long, onResult: (List<Map<String, Any?>>) -> Unit) {
        // Bluetooth off → scanning silently finds nothing (seen 2026-07-05).
        // Surface it in the console and pop the system enable dialog instead.
        if (!bluetoothEnabled()) {
            emit(
                "deviceEvent",
                mapOf("hex" to "Bluetooth is OFF — asking Android to enable it")
            )
            requestEnableBluetooth()
            main.post { onResult(emptyList()) }
            return
        }
        val hits = LinkedHashMap<String, Map<String, Any?>>()
        val filteredLogged = HashSet<String>()
        var finished = false

        fun finish() {
            if (finished) return
            finished = true
            try {
                BleScannerHelper.getInstance().stopScan(app)
            } catch (e: Exception) {
                // Scanner may already be stopped — the result list still counts.
            }
            main.post { onResult(hits.values.toList()) }
        }

        val callback = object : ScanWrapperCallback {
            override fun onStart() {}

            override fun onStop() = finish()

            override fun onLeScan(
                device: BluetoothDevice?,
                rssi: Int,
                scanRecord: ByteArray?,
            ) {
                val name = try {
                    device?.name
                } catch (e: SecurityException) {
                    null
                }
                if (device == null || name.isNullOrEmpty()) return
                // Faraz (2026-07-05): only the glasses in the connect list —
                // a nearby TV got tapped by mistake. L80x covers the
                // L801/L802 naming seen on hardware; everything else stays
                // visible in logcat for Stage A truth-keeping.
                if (!name.startsWith("L80")) {
                    if (filteredLogged.add(device.address)) {
                        Log.i(TAG, "scan filtered out: $name ${device.address} $rssi dBm")
                    }
                    return
                }
                hits[device.address] = mapOf(
                    "name" to name,
                    "mac" to device.address,
                    "rssi" to rssi,
                )
            }

            override fun onScanFailed(errorCode: Int) {
                emit("deviceEvent", mapOf("hex" to "scanFailed code=$errorCode"))
                finish()
            }

            override fun onParsedData(device: BluetoothDevice?, record: ScanRecord?) {}

            override fun onBatchScanResults(results: MutableList<android.bluetooth.le.ScanResult>?) {}
        }

        BleScannerHelper.getInstance().reSetCallback()
        BleScannerHelper.getInstance().scanDevice(app, null, callback)
        main.postDelayed({ finish() }, timeoutMs)
    }

    override fun connect(mac: String) {
        Log.i(TAG, "connect $mac")
        if (!bluetoothEnabled()) {
            emit(
                "deviceEvent",
                mapOf("hex" to "Bluetooth is OFF — cannot connect; asking Android to enable")
            )
            requestEnableBluetooth()
            // Reset the Lab's optimistic "connecting…" and remember the MAC —
            // the BT-on broadcast auto-reconnects to it.
            lastConnectionState = null
            emitConnectionState("disconnected")
            userDisconnected = false
            pendingMac = mac
            return
        }
        val op = BleOperateManager.getInstance()
        if (op.isConnected && pendingMac != null && pendingMac != mac) {
            // Switching devices (seen on hardware: user connected to a TV,
            // then tapped the glasses): tear the old link down first or
            // connectDirectly silently goes nowhere.
            Log.i(TAG, "switching device: unbinding $pendingMac first")
            op.setNeedConnect(false)
            op.unBindDevice()
        }
        // Reset the dedupe so this attempt emits a fresh "connected"
        // transition even if the previous link never reported disconnected.
        lastConnectionState = null
        userDisconnected = false
        pendingMac = mac
        op.connectDirectly(mac)
        armConnectWatchdog()
    }

    override fun disconnect() {
        Log.i(TAG, "disconnect (unBindDevice)")
        cancelConnectWatchdog()
        // Verified on hardware 2026-07-05: setNeedConnect(false)+disconnect()
        // is NOT enough — the SDK re-attaches within seconds. unBindDevice()
        // (the sample's disconnect button and the PDF's mapping) is the real
        // teardown.
        userDisconnected = true
        BleOperateManager.getInstance().setNeedConnect(false)
        BleOperateManager.getInstance().unBindDevice()
        pendingMac = null
    }

    override fun setAutoReconnect(enabled: Boolean) {
        autoReconnectEnabled = enabled
        BleOperateManager.getInstance().setNeedConnect(enabled)
        emit("deviceEvent", mapOf("hex" to "autoReconnect=$enabled"))
    }

    // -- Device data -------------------------------------------------------------

    override fun requestBattery() {
        LargeDataHandler.getInstance().syncBattery()
    }

    override fun requestDeviceInfo() {
        LargeDataHandler.getInstance().syncDeviceInfo { _, resp ->
            if (resp != null) {
                emit(
                    "deviceInfo",
                    mapOf(
                        "btFirmware" to resp.firmwareVersion,
                        "btHardware" to resp.hardwareVersion,
                        "wifiFirmware" to resp.wifiFirmwareVersion,
                        "wifiHardware" to resp.wifiHardwareVersion,
                    )
                )
            }
        }
    }

    /**
     * All glasses-initiated reports land here. loadData[6] is the report type
     * (vendor sample). Battery (0x05) maps to a typed event; everything else
     * is forwarded as `deviceEvent` with the raw payload hex — never swallowed
     * — plus a label where the sample documents the meaning. Camera/audio
     * codes get typed mappings in Tasks 2.4/2.5.
     */
    private val notifyListener = object : GlassesDeviceNotifyListener() {
        override fun parseData(cmdType: Int, response: GlassesDeviceNotifyRsp) {
            try {
                val load = response.loadData ?: return
                if (load.size < 7) {
                    emit("deviceEvent", mapOf("hex" to load.toHex()))
                    return
                }
                when (load[6].toInt()) {
                    0x05 -> emit(
                        "battery",
                        mapOf(
                            "pct" to load.getOrNull(7)?.toInt(),
                            "charging" to (load.getOrNull(8)?.toInt() == 1),
                        )
                    )
                    0x02 -> {
                        // AI photo captured on the glasses → pull thumbnail.
                        emit(
                            "deviceEvent",
                            mapOf("hex" to "aiPhotoTaken ${load.toHex()}")
                        )
                        fetchThumbnail()
                    }
                    0x0a -> {
                        // Tentative wear mapping: 0x0a is undocumented; seen
                        // while handling the glasses on 2026-07-05. Raw hex
                        // stays in logcat until hardware confirms.
                        Log.i(TAG, "0x0a raw ${load.toHex()}")
                        emit(
                            "wearState",
                            mapOf("worn" to (load.getOrNull(7)?.toInt() == 1))
                        )
                    }
                    else -> {
                        // Human-readable touch/gesture labels (Faraz request:
                        // "pata nahi chalta kya hua") — taps never reach the
                        // app (on-device only, no report API in the .aar);
                        // these are ALL the touches the SDK exposes.
                        val label = when (load[6].toInt()) {
                            // Seen on hardware: fires after each photo lands
                            // on the glasses' storage; load[7] = photo count.
                            0x01 -> "photoStored count=${load.getOrNull(7)?.toInt()}"
                            0x03 ->
                                if (load.getOrNull(7)?.toInt() == 1)
                                    "TOUCH long-press → glasses mic ON"
                                else "glasses mic state=${load.getOrNull(7)?.toInt()}"
                            0x04 -> "otaProgress"
                            0x0c -> "TOUCH pause gesture (voice broadcast paused)"
                            0x0d -> "unbindApp"
                            0x0e -> "glasses storage FULL"
                            0x10 -> "translationPause"
                            0x12 -> "TOUCH slide → volume " +
                                "music=${load.getOrNull(10)?.toInt()}/" +
                                "${load.getOrNull(9)?.toInt()} " +
                                "system=${load.getOrNull(18)?.toInt()}/" +
                                "${load.getOrNull(17)?.toInt()}"
                            else -> "unknown"
                        }
                        emit(
                            "deviceEvent",
                            mapOf("hex" to "$label ${load.toHex()}")
                        )
                    }
                }
            } catch (e: Exception) {
                emit("deviceEvent", mapOf("hex" to "notifyParseError: $e"))
            }
        }
    }

    private fun ByteArray.toHex(): String =
        joinToString(" ") { "%02x".format(it) }

    // -- Audio paths (Task 2.5) ---------------------------------------------

    /**
     * The vendor routes the LIVE glasses-mic PCM through the WiFi listener
     * (of all places): voiceFromGlasses(bytes) + voiceFromGlassesStatus(1/2).
     * The PCM format is Stage A's single most important unknown, so every
     * session is measured (bytes/sec) AND written to a .pcm file in DCIM_1
     * for offline analysis (adb pull → inspect/listen).
     */
    private val wifiListener = object : GlassesControl.WifiFilesDownloadListener {
        override fun voiceFromGlassesStatus(status: Int) {
            when (status) {
                1 -> {
                    pcmBytesTotal = 0
                    pcmChunks = 0
                    pcmStartMs = SystemClock.elapsedRealtime()
                    val f = File(albumDir, "lab_${System.currentTimeMillis()}.pcm")
                    pcmOut = FileOutputStream(f)
                    emit("audio", mapOf("status" to "glasses mic ON → ${f.name}"))
                }
                2 -> {
                    val secs =
                        (SystemClock.elapsedRealtime() - pcmStartMs) / 1000.0
                    val bytesPerSec =
                        if (secs > 0.2) (pcmBytesTotal / secs).toInt() else 0
                    // 16-bit mono assumption → rate = bytes/sec ÷ 2.
                    val estRate = bytesPerSec / 2
                    try {
                        pcmOut?.close()
                    } catch (e: Exception) {
                        Log.i(TAG, "pcm close: $e")
                    }
                    pcmOut = null
                    emit(
                        "audio",
                        mapOf(
                            "status" to "glasses mic OFF — $pcmBytesTotal B in " +
                                "${"%.1f".format(secs)}s = $bytesPerSec B/s " +
                                "(≈$estRate Hz if 16-bit mono)"
                        )
                    )
                }
                else -> emit("audio", mapOf("status" to "voiceStatus=$status"))
            }
        }

        override fun voiceFromGlasses(pcmData: ByteArray) {
            pcmBytesTotal += pcmData.size
            pcmChunks++
            try {
                pcmOut?.write(pcmData)
            } catch (e: Exception) {
                Log.i(TAG, "pcm write: $e")
            }
            if (audioMode == "pcm" || audioMode == "wake") {
                emit("pcmChunk", mapOf("bytes" to pcmData.size))
            }
        }

        // WiFi sync callbacks (Task 2.6a).
        override fun onGlassesControlSuccess() =
            emit("syncProgress", mapOf("file" to "WiFi sync started", "pct" to 0))

        override fun onGlassesFail(errorCode: Int) =
            emit("deviceEvent", mapOf("hex" to "wifi glassesFail err=$errorCode"))

        override fun wifiSpeed(wifiSpeed: String) {
            Log.i(TAG, "wifiSpeed $wifiSpeed")
            lastWifiSpeedKbps =
                Regex("[0-9]+(\\.[0-9]+)?").find(wifiSpeed)
                    ?.value?.toDoubleOrNull() ?: lastWifiSpeedKbps
        }

        override fun fileProgress(fileName: String, progress: Int) = emit(
            "syncProgress",
            mapOf(
                "file" to fileName,
                "pct" to progress,
                "speedKbps" to lastWifiSpeedKbps,
            )
        )

        override fun fileWasDownloadSuccessfully(
            entity: com.oudmon.wifi.bean.GlassAlbumEntity,
        ) = emit("deviceEvent", mapOf("hex" to "downloaded → $entity"))

        override fun fileCount(index: Int, total: Int) =
            emit("deviceEvent", mapOf("hex" to "sync file $index/$total"))

        override fun fileDownloadComplete() = emit(
            "syncProgress",
            mapOf(
                "file" to "all files done ✓",
                "pct" to 100,
                "speedKbps" to lastWifiSpeedKbps,
            )
        )

        override fun fileDownloadError(fileType: Int, errorType: Int) =
            emit("deviceEvent", mapOf("hex" to "sync error type=$fileType err=$errorType"))

        override fun eisEnd(fileName: String, filePath: String) =
            emit("deviceEvent", mapOf("hex" to "eis done $fileName"))

        override fun eisError(fileName: String, sourcePath: String, errorInfo: String) =
            emit("deviceEvent", mapOf("hex" to "eis error $fileName $errorInfo"))

        override fun recordingToPcm(fileName: String, filePath: String, duration: Int) =
            emit("deviceEvent", mapOf("hex" to "recordingToPcm $fileName ${duration}s"))

        override fun recordingToPcmError(fileName: String, errorInfo: String) =
            emit("deviceEvent", mapOf("hex" to "recordingToPcm error $errorInfo"))
    }

    /**
     * Classic-BT pairing per the integration guide (§3, sample-confirmed):
     * openBT() → classicBluetoothStartScan() → createBondBluetoothJieLi on
     * EVERY found device (the SDK matches the address itself).
     */
    private val classicBtReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.action) {
                BluetoothDevice.ACTION_FOUND -> {
                    @Suppress("DEPRECATION")
                    val device = intent.getParcelableExtra<BluetoothDevice>(
                        BluetoothDevice.EXTRA_DEVICE
                    ) ?: return
                    try {
                        BleOperateManager.getInstance().createBondBluetoothJieLi(device)
                    } catch (e: Exception) {
                        Log.i(TAG, "createBond: $e")
                    }
                }
                BluetoothDevice.ACTION_BOND_STATE_CHANGED -> {
                    val state = intent.getIntExtra(
                        BluetoothDevice.EXTRA_BOND_STATE, -1
                    )
                    if (state == BluetoothDevice.BOND_BONDED) {
                        try {
                            BleOperateManager.getInstance().classicBluetoothStopScan()
                        } catch (e: Exception) {
                            Log.i(TAG, "stop classic scan: $e")
                        }
                        emit("audio", mapOf("status" to "classic BT bonded ✓"))
                    } else {
                        emit("audio", mapOf("status" to "classic BT bondState=$state"))
                    }
                }
            }
        }
    }

    override fun pairClassicBt() {
        Log.i(TAG, "pairClassicBt")
        if (!classicBtReceiverRegistered) {
            val filter = IntentFilter().apply {
                addAction(BluetoothDevice.ACTION_FOUND)
                addAction(BluetoothDevice.ACTION_BOND_STATE_CHANGED)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                app.registerReceiver(classicBtReceiver, filter, Context.RECEIVER_EXPORTED)
            } else {
                app.registerReceiver(classicBtReceiver, filter)
            }
            classicBtReceiverRegistered = true
        }
        LargeDataHandler.getInstance().openBT()
        BleOperateManager.getInstance().classicBluetoothStartScan()
        emit("audio", mapOf("status" to "classic BT scanning…"))
    }

    override fun startAudioTest(mode: String) {
        Log.i(TAG, "startAudioTest $mode")
        audioMode = mode
        when (mode) {
            "pcm" -> emit(
                "audio",
                mapOf("status" to "PCM armed — long-press the glasses to talk")
            )
            "hfp" -> startHfpRecording()
            "tts" -> startTtsSample()
            "wake" -> {
                // Undocumented probe: aiVoiceWake exists in the .aar but not
                // the PDF. If it remotely opens the glasses mic we should see
                // voiceFromGlassesStatus(1) + PCM without any touch.
                emit("audio", mapOf("status" to "aiVoiceWake(true,true) sent — watching for mic…"))
                LargeDataHandler.getInstance().aiVoiceWake(true, true) { _, rsp ->
                    emit("audio", mapOf("status" to "aiVoiceWake ack: $rsp"))
                }
            }
        }
    }

    /**
     * 10 s recording from the phone's input while the HFP/SCO route is up —
     * if the glasses are classic-BT bonded, the SCO mic IS the glasses mic.
     * Tries 16 kHz (wideband/mSBC) first, falls back to 8 kHz (narrowband).
     */
    private fun startHfpRecording() {
        val am = app.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        @Suppress("DEPRECATION")
        am.startBluetoothSco()
        @Suppress("DEPRECATION")
        am.isBluetoothScoOn = true
        emit("audio", mapOf("status" to "SCO route requested, recording in 1 s…"))
        main.postDelayed({ recordHfp(am) }, 1000L)
    }

    private fun recordHfp(am: AudioManager) {
        val rate = intArrayOf(16000, 8000).firstOrNull {
            AudioRecord.getMinBufferSize(
                it, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
            ) > 0
        } ?: run {
            emit("audio", mapOf("status" to "hfp: no usable sample rate"))
            return
        }
        val bufSize = AudioRecord.getMinBufferSize(
            rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        ) * 4
        val recorder = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                rate, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, bufSize,
            )
        } catch (e: SecurityException) {
            emit("audio", mapOf("status" to "hfp: RECORD_AUDIO permission missing"))
            return
        }
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            emit("audio", mapOf("status" to "hfp: AudioRecord init failed @$rate Hz"))
            recorder.release()
            return
        }
        hfpRecording = true
        emit("audio", mapOf("status" to "hfp recording 10 s @$rate Hz…"))
        Thread {
            val f = File(albumDir, "lab_hfp_${System.currentTimeMillis()}_$rate.pcm")
            val out = FileOutputStream(f)
            val buf = ByteArray(4096)
            var total = 0L
            val end = SystemClock.elapsedRealtime() + 10_000
            recorder.startRecording()
            while (hfpRecording && SystemClock.elapsedRealtime() < end) {
                val n = recorder.read(buf, 0, buf.size)
                if (n > 0) {
                    out.write(buf, 0, n)
                    total += n
                }
            }
            recorder.stop()
            recorder.release()
            out.close()
            @Suppress("DEPRECATION")
            am.stopBluetoothSco()
            emit(
                "audio",
                mapOf("status" to "hfp done: $total B @$rate Hz → ${f.name}")
            )
        }.start()
    }

    /** Real speech to the glasses speaker over the A2DP media route. */
    private fun startTtsSample() {
        val engine = tts
        if (engine != null) {
            speakSample(engine)
            return
        }
        tts = TextToSpeech(app) { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale.US
                tts?.let { speakSample(it) }
            } else {
                emit("audio", mapOf("status" to "tts engine init failed ($status)"))
            }
        }
    }

    private fun speakSample(engine: TextToSpeech) {
        val spoken = engine.speak(
            "Hello Faraz. This is FarryOn speaking through your smart glasses. " +
                "If you can hear this clearly, the audio output path works.",
            TextToSpeech.QUEUE_FLUSH, null, "glasses_lab_tts",
        )
        emit(
            "audio",
            mapOf(
                "status" to if (spoken == TextToSpeech.SUCCESS)
                    "tts playing on the media route (A2DP → glasses if bonded)"
                else "tts speak failed ($spoken)"
            )
        )
    }

    override fun stopAudioTest() {
        Log.i(TAG, "stopAudioTest (was $audioMode)")
        val was = audioMode
        audioMode = null
        hfpRecording = false
        tts?.stop()
        if (was == "pcm") {
            try {
                GlassesControl.getInstance(app)?.stopGlassesVoice()
            } catch (e: Exception) {
                Log.i(TAG, "stopGlassesVoice: $e")
            }
        }
        emit("audio", mapOf("status" to "audio_test_stopped"))
    }

    // -- Camera (Task 2.4) -------------------------------------------------------

    /**
     * The glasses ack every control command with their current work mode;
     * anything except idle/photo means the command was ignored — surface it.
     */
    private fun describeWorkType(t: Int): String = when (t) {
        1, 6 -> "photoMode"
        2 -> "recordingVideo"
        4 -> "transferMode"
        5 -> "otaMode"
        7 -> "aiConversation"
        8 -> "audioRecording"
        else -> "workType=$t"
    }

    override fun takePhoto() {
        Log.i(TAG, "takePhoto")
        LargeDataHandler.getInstance().glassesControl(
            byteArrayOf(0x02, 0x01, 0x01)
        ) { _, rsp ->
            // err=-1/workType=0 is the SDK's neutral "command sent" ack
            // (hardware-verified) — only a positive errorCode is a refusal.
            if (rsp != null && rsp.errorCode > 0) {
                emit(
                    "deviceEvent",
                    mapOf(
                        "hex" to "takePhoto refused err=${rsp.errorCode} " +
                            describeWorkType(rsp.workTypeIng)
                    )
                )
            }
        }
    }

    override fun takeAiPhoto(requestId: String) {
        Log.i(TAG, "takeAiPhoto $requestId")
        photoRequestId = requestId
        photoStartMs = SystemClock.elapsedRealtime()
        // Sample's btnThumbnail payload; thumbnailSize range is 0..6 — 0x02
        // is the sample's default (resolution measured on hardware).
        val size: Byte = 0x02
        LargeDataHandler.getInstance().glassesControl(
            byteArrayOf(0x02, 0x01, 0x06, size, size, 0x02)
        ) { _, rsp ->
            // See takePhoto: err=-1 is the send ack, not a rejection.
            if (rsp != null && rsp.errorCode > 0) {
                emit(
                    "deviceEvent",
                    mapOf(
                        "hex" to "takeAiPhoto refused err=${rsp.errorCode} " +
                            describeWorkType(rsp.workTypeIng)
                    )
                )
            }
        }
    }

    /**
     * Notify 0x02 = AI photo captured → pull the JPEG thumbnail over BLE.
     *
     * Hardware-verified 2026-07-05: the callback STREAMS the JPEG in ~1013-
     * byte BLE chunks with the boolean=false, then fires one final time with
     * boolean=true carrying the remainder — accumulate everything, emit once.
     */
    private fun fetchThumbnail() {
        val buffer = java.io.ByteArrayOutputStream()
        LargeDataHandler.getInstance().getPictureThumbnails { _, done, data ->
            if (data != null && data.isNotEmpty()) buffer.write(data)
            if (!done) return@getPictureThumbnails
            val jpeg = buffer.toByteArray()
            val elapsed = (SystemClock.elapsedRealtime() - photoStartMs).toInt()
            Log.i(TAG, "thumbnail complete: ${jpeg.size} bytes in $elapsed ms")
            if (jpeg.isNotEmpty()) {
                emit(
                    "thumbnail",
                    mapOf(
                        // Device-initiated captures (gesture) have no request.
                        "requestId" to (photoRequestId ?: "device-initiated"),
                        "jpeg" to jpeg,
                        "elapsedMs" to if (photoRequestId != null) elapsed else -1,
                    )
                )
            } else {
                emit("deviceEvent", mapOf("hex" to "thumbnail fetch: 0 bytes"))
            }
            photoRequestId = null
        }
    }

    // -- WiFi media sync (Task 2.6a) ------------------------------------------

    override fun startWifiSync() {
        Log.i(TAG, "startWifiSync (importAlbum)")
        emit(
            "syncProgress",
            mapOf("file" to "WiFi-P2P pairing…", "pct" to 0, "speedKbps" to 0.0)
        )
        GlassesControl.getInstance(app)?.importAlbum()
    }

    override fun stopWifiSync() {
        // Verified against the .aar: the vendor exposes no cancel/stop for a
        // running importAlbum — the sync runs to completion.
        emit(
            "deviceEvent",
            mapOf("hex" to "stopWifiSync: vendor SDK has no cancel — sync runs to completion")
        )
    }

    override fun setVolume(type: String, level: Int) =
        notWired("setVolume:$type=$level", "2.5b")

    private fun notWired(command: String, task: String) {
        emit("deviceEvent", mapOf("hex" to "$command → not wired yet (Task $task)"))
    }

    // -- Boot / teardown --------------------------------------------------------

    // Mirrors the sample's MyApplication.initBle(). Declared last so every
    // property above (receiver, listeners) is initialized before use.
    init {
        LargeDataHandler.getInstance()
        BleOperateManager.getInstance(app).apply {
            setApplication(app)
            init()
        }
        BleBaseControl.getInstance(app).setmContext(app)
        localBroadcast(register = true, bleReceiver, BleAction.getIntentFilter())
        receiverRegistered = true
        // Adapter on/off is a protected system broadcast (mirrors the
        // sample's BluetoothReceiver registration).
        val btFilter = IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            app.registerReceiver(btStateReceiver, btFilter, Context.RECEIVER_EXPORTED)
        } else {
            app.registerReceiver(btStateReceiver, btFilter)
        }
        // Guide §2.3: the notify listener (slot 100) registers AFTER
        // onServiceDiscovered, not here. Battery callback is a passive map
        // entry — safe to add up front.
        LargeDataHandler.getInstance().addBatteryCallBack("glasses_lab") { _, resp ->
            if (resp != null) {
                emit(
                    "battery",
                    mapOf("pct" to resp.battery, "charging" to resp.isCharging)
                )
            }
        }
        // Guide §2.3 (sample's MainActivity.initListener): GlassesControl owns
        // the WiFi P2P path AND the live glasses-mic PCM stream.
        GlassesControl.getInstance(app)?.initGlasses(albumDir.absolutePath)
        GlassesControl.getInstance(app)?.setWifiDownloadListener(wifiListener)
    }

    override fun dispose() {
        cancelConnectWatchdog()
        stopAudioTest()
        tts?.shutdown()
        tts = null
        try {
            app.unregisterReceiver(btStateReceiver)
        } catch (e: Exception) {
            Log.i(TAG, "unregister btStateReceiver: $e")
        }
        if (classicBtReceiverRegistered) {
            try {
                app.unregisterReceiver(classicBtReceiver)
            } catch (e: Exception) {
                Log.i(TAG, "unregister classicBtReceiver: $e")
            }
            classicBtReceiverRegistered = false
        }
        try {
            BleScannerHelper.getInstance().stopScan(app)
        } catch (e: Exception) {
            // Already stopped.
        }
        LargeDataHandler.getInstance().removeOutDeviceListener(100)
        LargeDataHandler.getInstance().removeBatteryCallBack("glasses_lab")
        if (receiverRegistered) {
            try {
                localBroadcast(register = false, bleReceiver)
            } catch (e: Exception) {
                // Reflection target gone — nothing left to unregister.
            }
            receiverRegistered = false
        }
        listener = null
    }
}
