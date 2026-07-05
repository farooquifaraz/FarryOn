package com.farryon.farryon.glasses

import android.app.Application
import android.bluetooth.BluetoothDevice
import android.content.BroadcastReceiver
import android.content.Context
import android.content.IntentFilter
import android.os.Handler
import android.os.Looper
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
            if (!wasConnected) requestBattery()
        }
    }

    override fun scan(timeoutMs: Long, onResult: (List<Map<String, Any?>>) -> Unit) {
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
                    else -> {
                        val label = when (load[6].toInt()) {
                            0x02 -> "aiPhotoTaken"
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

    // -- Not yet wired (Tasks 2.4–2.6) — visible in the console, never crash. ----

    override fun takePhoto() = notWired("takePhoto", "2.4")

    override fun takeAiPhoto(requestId: String) = notWired("takeAiPhoto", "2.4")

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
