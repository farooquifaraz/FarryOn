package com.farryon.farryon.glasses

import android.app.Application
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
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

    /** In-flight AI photo: request id + t0 for the capture→thumbnail latency. */
    private var photoRequestId: String? = null
    private var photoStartMs: Long = 0

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
            // Populate the Device info card without an extra Refresh tap —
            // once per transition, not on every re-broadcast.
            if (!wasConnected) {
                requestBattery()
                // Wear reporting is OFF by default (verified 2026-07-05: wear
                // on/off emitted nothing) — enable it on every fresh link.
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
        val adapter =
            (app.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
        if (adapter?.isEnabled != true) {
            emit(
                "deviceEvent",
                mapOf("hex" to "Bluetooth is OFF — asking Android to enable it")
            )
            try {
                app.startActivity(
                    Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            } catch (e: Exception) {
                // BLUETOOTH_CONNECT revoked or no dialog available — the
                // console line above still tells the user what to do.
                Log.i(TAG, "enable-BT dialog failed: $e")
            }
            main.post { onResult(emptyList()) }
            return
        }
        val hits = LinkedHashMap<String, Map<String, Any?>>()
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
                    Log.i(TAG, "scan filtered out: $name ${device.address} $rssi dBm")
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
    }

    override fun disconnect() {
        Log.i(TAG, "disconnect (unBindDevice)")
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
                        val label = when (load[6].toInt()) {
                            0x03 -> "micStateChange"
                            0x04 -> "otaProgress"
                            0x0c -> "voicePauseEvent"
                            0x0d -> "unbindApp"
                            0x0e -> "lowMemory"
                            0x10 -> "translationPause"
                            0x12 -> "volumeChange"
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
            if (rsp != null) {
                emit(
                    "deviceEvent",
                    mapOf(
                        "hex" to "takePhoto ack err=${rsp.errorCode} " +
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
            if (rsp != null && rsp.errorCode != 0) {
                emit(
                    "deviceEvent",
                    mapOf(
                        "hex" to "takeAiPhoto REJECTED err=${rsp.errorCode} " +
                            describeWorkType(rsp.workTypeIng)
                    )
                )
            }
        }
    }

    /** Notify 0x02 = AI photo captured → pull the JPEG thumbnail over BLE. */
    private fun fetchThumbnail() {
        LargeDataHandler.getInstance().getPictureThumbnails { _, success, data ->
            val elapsed = (SystemClock.elapsedRealtime() - photoStartMs).toInt()
            if (success && data != null && data.isNotEmpty()) {
                emit(
                    "thumbnail",
                    mapOf(
                        // Device-initiated captures (gesture) have no request.
                        "requestId" to (photoRequestId ?: "device-initiated"),
                        "jpeg" to data,
                        "elapsedMs" to if (photoRequestId != null) elapsed else -1,
                    )
                )
            } else {
                emit(
                    "deviceEvent",
                    mapOf("hex" to "thumbnail fetch FAILED success=$success " +
                        "bytes=${data?.size ?: 0}")
                )
            }
            photoRequestId = null
        }
    }

    // -- Not yet wired (Tasks 2.5–2.6) — visible in the console, never crash. ----

    override fun pairClassicBt() = notWired("pairClassicBt", "2.5")

    override fun startAudioTest(mode: String) = notWired("startAudioTest:$mode", "2.5")

    override fun stopAudioTest() = notWired("stopAudioTest", "2.5")

    override fun startWifiSync() = notWired("startWifiSync", "2.6")

    override fun stopWifiSync() = notWired("stopWifiSync", "2.6")

    override fun setVolume(type: String, level: Int) =
        notWired("setVolume:$type=$level", "2.5")

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
        // 100 mirrors the sample's listener slot for glasses notify reports.
        LargeDataHandler.getInstance().addOutDeviceListener(100, notifyListener)
        LargeDataHandler.getInstance().addBatteryCallBack("glasses_lab") { _, resp ->
            if (resp != null) {
                emit(
                    "battery",
                    mapOf("pct" to resp.battery, "charging" to resp.isCharging)
                )
            }
        }
    }

    override fun dispose() {
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
