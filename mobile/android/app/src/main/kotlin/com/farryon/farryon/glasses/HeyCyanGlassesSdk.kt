package com.farryon.farryon.glasses

import android.app.Application
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.ContentValues
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
import android.provider.MediaStore
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
import com.oudmon.ble.base.communication.file.FileHandle
import com.oudmon.ble.base.communication.file.SimpleCallback
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

        // -- Tuning knobs (all timing/protocol values live here, never inline) --

        /** BLE connect budget before the watchdog declares failure. A healthy
         *  connect is ~2.5 s (median, 11 samples); only a degraded stack takes
         *  ~20 s, so this window rescues those instead of flashing
         *  "disconnected" prematurely. */
        private const val CONNECT_TIMEOUT_MS = 28_000L

        /** Total connect attempts (1 first try + silent retries). */
        private const val CONNECT_MAX_ATTEMPTS = 2

        /** Pause between the clean-slate unBindDevice() and the fresh
         *  connectDirectly(). A wedged pending connect only clears via
         *  unBindDevice (log-proven 2026-07-11: five overlapping connects hung
         *  for 3 min; a manual disconnect→connect then linked in 4.9 s) — the
         *  pause lets the SDK finish tearing down before the new attempt. */
        private const val CONNECT_CLEAN_SLATE_DELAY_MS = 500L

        /** Wait after the phone's Bluetooth turns ON before auto-reconnecting.
         *  Firing connectDirectly on the STATE_ON broadcast itself wedges the
         *  attempt (seen 2026-07-11 19:47): the BLE stack is still booting and
         *  the glasses' classic-BT (A2DP) reattach contends with the LE radio. */
        private const val BT_ON_RECONNECT_DELAY_MS = 2_500L

        /** AI-photo budget from the BLE command to the capture notify (0x02).
         *  Capture itself is ~2.2-2.4 s (firmware-fixed); busy glasses ignore
         *  the command silently, which this watchdog turns into a report. */
        private const val PHOTO_CAPTURE_TIMEOUT_MS = 8_000L

        /** Rolling per-chunk budget while the JPEG thumbnail streams over BLE
         *  (~1013-byte chunks, ~10 kB/s measured). Re-armed on every chunk, so
         *  it only fires when the transfer genuinely stalls. */
        private const val THUMBNAIL_CHUNK_TIMEOUT_MS = 3_000L

        /** Vendor thumbnailSize argument (range 0x00..0x06). 0x02 measures
         *  512×384 / 15-33 KB — recognition-grade; lower is faster. */
        private const val THUMBNAIL_SIZE: Byte = 0x02

        /** requestId used for captures the glasses started themselves
         *  (touch gesture) — there is no app-side request to correlate. */
        private const val DEVICE_INITIATED_REQUEST_ID = "device-initiated"

        /** WiFi sync stall budget: importAlbum fails silently when the
         *  glasses' WiFi-P2P never comes up (e.g. on the charger). */
        private const val WIFI_SYNC_STALL_TIMEOUT_MS = 60_000L

        /** Media-count probe reply budget before importing anyway. */
        private const val MEDIA_COUNT_PROBE_TIMEOUT_MS = 3_000L

        /** The glasses mark synced media as done LAZILY (still stale 1.5 s
         *  after completion) — re-query twice; the later result wins. */
        private const val MEDIA_RECOUNT_FIRST_DELAY_MS = 3_000L
        private const val MEDIA_RECOUNT_SECOND_DELAY_MS = 10_000L

        /** HFP mic probe: SCO route settle time + recording duration. */
        private const val HFP_SCO_SETTLE_MS = 1_000L
        private const val HFP_RECORD_DURATION_MS = 10_000L

        /** Delay after connect before sweeping aged retention files, so the BLE
         *  MTU (which the delete command needs) is negotiated first. */
        private const val RETENTION_SWEEP_DELAY_MS = 5_000L
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
    private var photoWatchdog: Runnable? = null

    private fun cancelPhotoWatchdog() {
        photoWatchdog?.let(main::removeCallbacks)
        photoWatchdog = null
    }

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

    /** A connect attempt is in flight exactly while its watchdog is armed.
     *  Used to serialize attempts: overlapping connectDirectly calls wedge
     *  the vendor SDK until unBindDevice (log-proven 2026-07-11). */
    private val isConnecting: Boolean
        get() = connectWatchdog != null

    private fun bluetoothEnabled(): Boolean =
        (app.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)
            ?.adapter?.isEnabled == true

    /**
     * Glasses already paired in Android's Bluetooth settings (classic-BT, for
     * audio). Verified 2026-07-10: a unit the user pairs there holds an A2DP
     * link and STOPS BLE-advertising, so [scan] finds nothing and the app can
     * never bind it. But we already know its MAC from the bond list, and
     * [BleOperateManager.connectDirectly] connects by MAC without needing an
     * advertisement — so we seed these into the scan results to make the glasses
     * connectable anyway. Name is normalized (strip spaces + uppercase) so
     * "L 801_DD8A" matches the L80x family like everything else.
     */
    /** Is this bonded device currently connected over classic BT (powered on
     *  and in range)? Uses the hidden BluetoothDevice.isConnected(). */
    private fun isClassicConnected(device: BluetoothDevice): Boolean {
        return try {
            val m = device.javaClass.getMethod("isConnected")
            (m.invoke(device) as? Boolean) == true
        } catch (e: Exception) {
            false
        }
    }

    private fun bondedGlasses(): List<Map<String, Any?>> {
        return try {
            val adapter = (app.getSystemService(Context.BLUETOOTH_SERVICE)
                as? BluetoothManager)?.adapter ?: return emptyList()
            adapter.bondedDevices.orEmpty().mapNotNull { device ->
                val name = try { device.name } catch (e: SecurityException) { null }
                if (name.isNullOrEmpty()) return@mapNotNull null
                if (!name.replace(" ", "").uppercase().startsWith("L80")) {
                    return@mapNotNull null
                }
                mapOf<String, Any?>(
                    "name" to name,
                    "mac" to device.address,
                    "rssi" to null,
                    "bonded" to true,
                    // Which unit is powered on RIGHT NOW (classic link up) — the
                    // one the user is actually wearing when two are paired.
                    "connected" to isClassicConnected(device),
                )
            }
        } catch (e: Exception) {
            emptyList()
        }
    }

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

    /** Attempts used for the in-flight connect (1 = first try). One silent
     *  retry recovers a slow/failed connect (e.g. a degraded BLE stack after
     *  heavy connect/disconnect cycling) without the user doing anything. */
    private var connectAttempt = 0

    /**
     * Clean-slate connect: unbind whatever half-open attempt the SDK holds,
     * pause, then issue a fresh connectDirectly. This is the ONLY sequence
     * that recovers a wedged pending connect (log-proven 2026-07-11), and it
     * is also safe on a healthy stack (unbind of nothing is a no-op).
     */
    private fun cleanSlateConnect(mac: String) {
        try {
            BleOperateManager.getInstance().unBindDevice()
        } catch (e: Exception) {
            Log.i(TAG, "pre-connect unbind: $e")
        }
        main.postDelayed({
            // Abandon if the user disconnected / switched device / lost
            // Bluetooth meanwhile.
            if (userDisconnected || pendingMac != mac) return@postDelayed
            if (lastConnectionState == "connected" || !bluetoothEnabled()) {
                return@postDelayed
            }
            BleOperateManager.getInstance().connectDirectly(mac)
        }, CONNECT_CLEAN_SLATE_DELAY_MS)
        armConnectWatchdog()
    }

    private fun armConnectWatchdog() {
        cancelConnectWatchdog()
        val r = Runnable {
            connectWatchdog = null // this attempt is over; allow a fresh one
            if (lastConnectionState == "connected") return@Runnable
            val mac = pendingMac
            if (!userDisconnected && mac != null && connectAttempt < CONNECT_MAX_ATTEMPTS) {
                // Timed out: retry silently before declaring failure (see
                // CONNECT_TIMEOUT_MS for the window rationale). The retry is
                // clean-slate — a bare connectDirectly on top of the stuck
                // attempt never recovers (log-proven 2026-07-11).
                connectAttempt++
                Log.i(TAG, "connect retry #$connectAttempt (clean slate) → $mac")
                emit("deviceEvent", mapOf("hex" to "connect slow — retrying ($mac)"))
                cleanSlateConnect(mac)
                return@Runnable
            }
            emit(
                "deviceEvent",
                mapOf("hex" to "connect timeout — glasses off / out of range?")
            )
            emitConnectionState("disconnected")
        }
        connectWatchdog = r
        main.postDelayed(r, CONNECT_TIMEOUT_MS)
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
                        // NOT immediately: the BLE stack is still booting and
                        // the glasses' A2DP reattach contends with the LE
                        // radio — an instant connectDirectly wedges (see
                        // BT_ON_RECONNECT_DELAY_MS).
                        emit(
                            "deviceEvent",
                            mapOf(
                                "hex" to "auto-reconnecting to $mac in " +
                                    "${BT_ON_RECONNECT_DELAY_MS} ms"
                            )
                        )
                        main.postDelayed({
                            if (userDisconnected || pendingMac != mac) {
                                return@postDelayed
                            }
                            if (lastConnectionState == "connected" || isConnecting) {
                                return@postDelayed
                            }
                            connectAttempt = 1
                            BleOperateManager.getInstance().connectDirectly(mac)
                            armConnectWatchdog()
                        }, BT_ON_RECONNECT_DELAY_MS)
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
                "connected" -> {
                    GlassesForegroundService.start(
                        app, DeviceManager.getInstance().deviceName ?: "L801"
                    )
                    // Prune any photos that aged past the retention window while we
                    // were away. Delayed so the BLE MTU is negotiated first (the
                    // delete command rides the same file-transfer channel).
                    main.postDelayed({ sweepRetention() }, RETENTION_SWEEP_DELAY_MS)
                }
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
                // Once per FRESH transition only. The SDK re-broadcasts
                // service-discovered every ~2.5 s on a live link, and repeat
                // initEnable/listener registrations STACK inside the vendor
                // SDK — every device report then arrives N times (seen
                // 2026-07-11: battery/photoStored/wear all exactly 2x, which
                // also double-fires fetchThumbnail on notify 0x02).
                LargeDataHandler.getInstance().initEnable()
                // Remember the device: the Lab lists it instantly on next
                // open so Connect works without an 8 s scan (guide §3:
                // saved MAC + connectDirectly).
                app.getSharedPreferences("glasses_lab", Context.MODE_PRIVATE)
                    .edit()
                    .putString(
                        "last_mac",
                        pendingMac ?: DeviceManager.getInstance().deviceAddress,
                    )
                    .putString(
                        "last_name",
                        DeviceManager.getInstance().deviceName ?: "L801",
                    )
                    .apply()
                // Idempotent: remove before add, so reconnect cycles never
                // leave a second listener behind (the duplicate-event bug).
                LargeDataHandler.getInstance().removeOutDeviceListener(100)
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
        if (isConnecting) {
            // A BLE scan disrupts a pending GATT connect (radio contention) —
            // seen 2026-07-11: scans issued mid-connect helped keep the
            // attempt wedged. Let the connect finish or time out first.
            emit(
                "deviceEvent",
                mapOf("hex" to "connect in progress — scan skipped; retry after it finishes")
            )
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
            // Fold in glasses already paired in Android BT settings. A unit
            // that's busy on classic-BT audio doesn't BLE-advertise, so the
            // scan above misses it — but we can still connectDirectly by the
            // bonded MAC. Don't clobber a live advertisement (keep the one
            // with a real rssi), just add any bonded L80x the scan didn't see.
            for (b in bondedGlasses()) {
                val mac = b["mac"] as? String ?: continue
                if (!hits.containsKey(mac)) hits[mac] = b
            }
            if (hits.isEmpty()) {
                emit(
                    "deviceEvent",
                    mapOf(
                        "hex" to "scan found no glasses — turn the glasses on and " +
                            "off-body wear sensor, or pair them once in Android " +
                            "Bluetooth settings so the app can connect directly"
                    )
                )
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
                // 2026-07-10: a second unit advertises as "L 801_DD8A" (with a
                // space), which "L80" prefix missed — normalize out whitespace
                // and uppercase before matching so all L80x variants pass.
                val norm = name.replace(" ", "").uppercase()
                if (!norm.startsWith("L80")) {
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
        if (op.isConnected && pendingMac == mac && lastConnectionState == "connected") {
            // Already connected to this device (hit on-device 2026-07-06:
            // re-tapping Connect on a live link tore it down and the SDK
            // needed a power-cycle to recover) — just re-assert the state.
            Log.i(TAG, "already connected to $mac — re-emitting state")
            emit("connectionState", mapOf("state" to "connected", "mac" to mac))
            return
        }
        if (isConnecting && pendingMac == mac) {
            // Serialize attempts: stacking another connectDirectly on top of
            // the pending one wedges the SDK until unBindDevice (log-proven
            // 2026-07-11: five overlapping connects hung for 3 minutes). The
            // in-flight attempt already has a clean-slate retry + timeout.
            Log.i(TAG, "connect: attempt to $mac already in progress — ignored")
            emit("deviceEvent", mapOf("hex" to "already connecting to $mac — hang on"))
            return
        }
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
        connectAttempt = 1
        // Clean slate (unbind → pause → connect): recovers a wedged pending
        // attempt, and also covers the silent-reconnect case where the SDK
        // holds a live link the Lab never saw (connectDirectly on top of one
        // tears it down and then fails until a power-cycle, LAB_NOTES 07-06).
        cleanSlateConnect(mac)
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
                    0x09 -> {
                        // Wear on/off — the unified code per the vendor
                        // (Tina, 2026-07-08). load[7]==1 → worn, 0 → removed.
                        // (Earlier we wrongly mapped 0x0a; that is internal
                        // hardware-debug, and 0x0b is the heartbeat.)
                        Log.i(TAG, "0x09 wear ${load.toHex()}")
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
                            // Vendor (Tina): 0x0a = internal HW-sensor debug,
                            // 0x0b = periodic heartbeat — neither is actionable.
                            0x0a -> "hwSensorDebug (internal)"
                            0x0b -> "heartbeat"
                            0x0c -> "TOUCH pause gesture (voice broadcast paused)"
                            0x0d -> "unbindApp"
                            0x0e -> {
                                // Retention "When full": the glasses are out of
                                // space — sync everything to the phone, then the
                                // per-file retention hook purges it off the
                                // glasses (fullCleanupActive makes it purge all).
                                if (retentionDays == -1 && !syncActive) {
                                    fullCleanupActive = true
                                    syncActive = true
                                    armSyncWatchdog()
                                    GlassesControl.getInstance(app)?.importAlbum()
                                }
                                "glasses storage FULL"
                            }
                            0x10 -> "translationPause"
                            0x12 -> {
                                // Volume-change reports carry the full block
                                // — prime the cache so app-side setVolume is
                                // a single write.
                                if (load.size > 19) {
                                    volCache = intArrayOf(
                                        load[8].toInt(), load[9].toInt(),
                                        load[10].toInt(), load[12].toInt(),
                                        load[13].toInt(), load[14].toInt(),
                                        load[16].toInt(), load[17].toInt(),
                                        load[18].toInt(), load[19].toInt(),
                                    )
                                }
                                "TOUCH slide → volume " +
                                    "music=${load.getOrNull(10)?.toInt()}/" +
                                    "${load.getOrNull(9)?.toInt()} " +
                                    "system=${load.getOrNull(18)?.toInt()}/" +
                                    "${load.getOrNull(17)?.toInt()}"
                            }
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
                // B0: carry the actual audio — Stage B's voice pipeline
                // streams these bytes Dart → backend → Whisper. The Lab
                // keeps using only the count.
                emit(
                    "pcmChunk",
                    mapOf("bytes" to pcmData.size, "data" to pcmData)
                )
            }
        }

        // WiFi sync callbacks (Task 2.6a). Every sign of life pushes the
        // stall watchdog forward; terminal callbacks clear it.
        override fun onGlassesControlSuccess() {
            armSyncWatchdog()
            emit("syncProgress", mapOf("file" to "WiFi sync started", "pct" to 0))
        }

        override fun onGlassesFail(errorCode: Int) {
            cancelSyncWatchdog()
            syncActive = false
            emit("deviceEvent", mapOf("hex" to "wifi glassesFail err=$errorCode"))
            emit(
                "syncProgress",
                mapOf("file" to "sync failed (err=$errorCode)", "pct" to 100, "speedKbps" to 0.0)
            )
        }

        override fun wifiSpeed(wifiSpeed: String) {
            Log.i(TAG, "wifiSpeed $wifiSpeed")
            lastWifiSpeedKbps =
                Regex("[0-9]+(\\.[0-9]+)?").find(wifiSpeed)
                    ?.value?.toDoubleOrNull() ?: lastWifiSpeedKbps
        }

        override fun fileProgress(fileName: String, progress: Int) {
            armSyncWatchdog()
            emit(
                "syncProgress",
                mapOf(
                    "file" to fileName,
                    "pct" to progress,
                    "speedKbps" to lastWifiSpeedKbps,
                )
            )
        }

        override fun fileWasDownloadSuccessfully(
            entity: com.oudmon.wifi.bean.GlassAlbumEntity,
        ) {
            armSyncWatchdog()
            emit("deviceEvent", mapOf("hex" to "downloaded ${entity.fileName}"))
            exportToGallery(entity.filePath, entity.fileName)
            // Now that it's safely on the phone, honour the retention policy —
            // free the glasses' storage by deleting old (or, on a full purge,
            // all) synced photos.
            applyRetention(entity)
        }

        override fun fileCount(index: Int, total: Int) {
            armSyncWatchdog()
            emit("deviceEvent", mapOf("hex" to "sync file $index/$total"))
        }

        override fun fileDownloadComplete() {
            cancelSyncWatchdog()
            syncActive = false
            fullCleanupActive = false // a storage-full purge (if any) is done
            emit(
                "syncProgress",
                mapOf(
                    "file" to "all files done ✓",
                    "pct" to 100,
                    "speedKbps" to lastWifiSpeedKbps,
                )
            )
            // Show the fresh glasses-memory state (normally 0). The glasses
            // mark media as synced LAZILY (measured: still stale 1.5 s after
            // completion), so query twice — the later one wins in the card.
            main.postDelayed({ refreshMediaCount() }, MEDIA_RECOUNT_FIRST_DELAY_MS)
            main.postDelayed({ refreshMediaCount() }, MEDIA_RECOUNT_SECOND_DELAY_MS)
        }

        override fun fileDownloadError(fileType: Int, errorType: Int) {
            armSyncWatchdog()
            emit("deviceEvent", mapOf("hex" to "sync error type=$fileType err=$errorType"))
        }

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
     * Every synced file is COPIED into the user's gallery automatically
     * (MediaStore insert → DCIM/FarryOn) the moment its download completes.
     * The SDK's private DCIM_1 copy stays untouched — it is the vendor's
     * working set (media.config bookkeeping), so no cut/move.
     */
    private fun exportToGallery(path: String?, name: String?) {
        if (path.isNullOrEmpty() || name.isNullOrEmpty()) return
        Thread {
            try {
                val src = File(path)
                if (!src.exists()) {
                    Log.i(TAG, "gallery export: missing $path")
                    return@Thread
                }
                val ext = name.substringAfterLast('.', "").lowercase()
                val isVideo = ext in listOf("mp4", "avi", "mov")
                val mime = when (ext) {
                    "jpg", "jpeg" -> "image/jpeg"
                    "png" -> "image/png"
                    "mp4" -> "video/mp4"
                    "avi" -> "video/x-msvideo"
                    "mov" -> "video/quicktime"
                    else -> {
                        Log.i(TAG, "gallery export: skipping non-media $name")
                        return@Thread
                    }
                }
                val collection =
                    if (isVideo) MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                    else MediaStore.Images.Media.EXTERNAL_CONTENT_URI
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                    put(MediaStore.MediaColumns.MIME_TYPE, mime)
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        put(MediaStore.MediaColumns.RELATIVE_PATH, "DCIM/FarryOn")
                    }
                }
                val uri = app.contentResolver.insert(collection, values)
                    ?: return@Thread
                app.contentResolver.openOutputStream(uri)?.use { out ->
                    src.inputStream().use { it.copyTo(out) }
                }
                emit("deviceEvent", mapOf("hex" to "gallery ← $name"))
            } catch (e: Exception) {
                Log.i(TAG, "gallery export failed: $e")
                emit(
                    "deviceEvent",
                    mapOf("hex" to "gallery export failed: ${e.message}")
                )
            }
        }.start()
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
        main.postDelayed({ recordHfp(am) }, HFP_SCO_SETTLE_MS)
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
            val end = SystemClock.elapsedRealtime() + HFP_RECORD_DURATION_MS
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
            "Hello Faraz. This is Farry speaking through your smart glasses. " +
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

    /**
     * Report a failed capture to Dart as a typed `captureFailed` event
     * (machine-readable reason, correlated by requestId) and ALSO emit the
     * empty-thumbnail marker that clears the Lab's capturing spinner.
     * Reason codes are the wire contract shared with the app and the backend
     * (`GlassesCaptureFailure` / PROTOCOL.md `capture_failed`).
     */
    private fun emitCaptureFailed(requestId: String, reason: String, detail: String) {
        emit(
            "captureFailed",
            mapOf("requestId" to requestId, "reason" to reason, "detail" to detail)
        )
        emit(
            "thumbnail",
            mapOf("requestId" to requestId, "jpeg" to ByteArray(0), "elapsedMs" to -1)
        )
    }

    override fun takeAiPhoto(requestId: String) {
        Log.i(TAG, "takeAiPhoto $requestId")
        // Single in-flight capture: the SDK has ONE thumbnail callback slot, so
        // a second command mid-capture would corrupt/mix the streamed JPEG.
        // Report the duplicate as busy; the first capture keeps its budget.
        if (photoRequestId != null) {
            Log.i(TAG, "takeAiPhoto: capture $photoRequestId already in flight")
            emitCaptureFailed(
                requestId, "busy",
                "another capture ($photoRequestId) is already in flight"
            )
            return
        }
        photoRequestId = requestId
        photoStartMs = SystemClock.elapsedRealtime()
        // Busy glasses (e.g. stuck in WiFi/transfer mode) silently ignore the
        // command — without this the Lab shows "capturing…" forever
        // (hit on-device 2026-07-06 23:38).
        cancelPhotoWatchdog()
        val watchdog = Runnable {
            if (photoRequestId != requestId) return@Runnable
            photoRequestId = null
            emitCaptureFailed(
                requestId, "capture_timeout",
                "no capture notify within ${PHOTO_CAPTURE_TIMEOUT_MS} ms — " +
                    "glasses busy (WiFi/transfer/recording mode?)"
            )
        }
        photoWatchdog = watchdog
        main.postDelayed(watchdog, PHOTO_CAPTURE_TIMEOUT_MS)
        // Sample's btnThumbnail payload; THUMBNAIL_SIZE is the sample's default
        // (resolution measured on hardware).
        LargeDataHandler.getInstance().glassesControl(
            byteArrayOf(0x02, 0x01, 0x06, THUMBNAIL_SIZE, THUMBNAIL_SIZE, 0x02)
        ) { _, rsp ->
            // See takePhoto: err=-1 is the send ack, not a rejection.
            if (rsp != null && rsp.errorCode > 0) {
                Log.i(TAG, "takeAiPhoto refused err=${rsp.errorCode}")
                cancelPhotoWatchdog()
                if (photoRequestId == requestId) photoRequestId = null
                emitCaptureFailed(
                    requestId, "busy",
                    "refused err=${rsp.errorCode} " +
                        describeWorkType(rsp.workTypeIng)
                )
            }
        }
    }

    /** Rolling watchdog for the BLE thumbnail transfer (re-armed per chunk). */
    private var thumbnailWatchdog: Runnable? = null

    /** Generation counter: invalidates a stalled/superseded transfer so a
     *  late SDK callback can never emit into a newer request's stream. */
    @Volatile private var thumbnailFetchGen = 0

    /** True while a thumbnail transfer is running. The glasses sometimes fire
     *  the "photo captured" notify (0x02) TWICE for one photo (hardware-seen
     *  2026-07-11); a second getPictureThumbnails() call mid-transfer resets
     *  the SDK's single stream and stalls it. Guard so the duplicate notify is
     *  ignored while a transfer is already in flight. */
    @Volatile private var thumbnailFetchActive = false

    private fun cancelThumbnailWatchdog() {
        thumbnailWatchdog?.let(main::removeCallbacks)
        thumbnailWatchdog = null
    }

    private fun armThumbnailWatchdog(gen: Int, requestId: String) {
        cancelThumbnailWatchdog()
        val r = Runnable {
            if (gen != thumbnailFetchGen) return@Runnable
            // Abort: bump the generation so late chunks are ignored.
            thumbnailFetchGen++
            thumbnailFetchActive = false
            photoRequestId = null
            Log.i(TAG, "thumbnail transfer stalled ($requestId)")
            emitCaptureFailed(
                requestId, "transfer_stalled",
                "no thumbnail chunk within ${THUMBNAIL_CHUNK_TIMEOUT_MS} ms"
            )
        }
        thumbnailWatchdog = r
        main.postDelayed(r, THUMBNAIL_CHUNK_TIMEOUT_MS)
    }

    /**
     * Notify 0x02 = AI photo captured → pull the JPEG thumbnail over BLE.
     *
     * Hardware-verified 2026-07-05: the callback STREAMS the JPEG in ~1013-
     * byte BLE chunks with the boolean=false, then fires one final time with
     * boolean=true carrying the remainder — accumulate everything, emit once.
     * A rolling per-chunk watchdog turns a mid-stream stall (BLE drop) into a
     * typed failure instead of a silent forever-hang.
     */
    private fun fetchThumbnail() {
        // Ignore a duplicate "photo captured" notify while a transfer is
        // already running: a second getPictureThumbnails() resets the SDK's
        // single stream and stalls it (hardware-seen 2026-07-11 — 0x02 fired
        // twice, killing the transfer).
        if (thumbnailFetchActive) {
            Log.i(TAG, "fetchThumbnail: transfer already in flight — ignoring duplicate notify")
            return
        }
        cancelPhotoWatchdog()
        thumbnailFetchActive = true
        // Device-initiated captures (touch gesture) have no app-side request.
        val requestId = photoRequestId ?: DEVICE_INITIATED_REQUEST_ID
        val gen = ++thumbnailFetchGen
        val buffer = java.io.ByteArrayOutputStream()
        armThumbnailWatchdog(gen, requestId)
        LargeDataHandler.getInstance().getPictureThumbnails { _, done, data ->
            if (gen != thumbnailFetchGen) return@getPictureThumbnails // aborted
            if (data != null && data.isNotEmpty()) buffer.write(data)
            if (!done) {
                armThumbnailWatchdog(gen, requestId)
                return@getPictureThumbnails
            }
            cancelThumbnailWatchdog()
            thumbnailFetchActive = false
            val jpeg = buffer.toByteArray()
            val elapsed = (SystemClock.elapsedRealtime() - photoStartMs).toInt()
            Log.i(TAG, "thumbnail complete: ${jpeg.size} bytes in $elapsed ms")
            if (jpeg.isNotEmpty()) {
                // Persist for test 3.3 (thumbnail → AI recognition offline):
                // exactly the bytes Stage B would send to Gemini Vision.
                try {
                    File(albumDir, "thumb_${System.currentTimeMillis()}.jpg")
                        .writeBytes(jpeg)
                } catch (e: Exception) {
                    Log.i(TAG, "thumb save: $e")
                }
                emit(
                    "thumbnail",
                    mapOf(
                        "requestId" to requestId,
                        "jpeg" to jpeg,
                        "elapsedMs" to if (photoRequestId != null) elapsed else -1,
                    )
                )
            } else {
                emitCaptureFailed(requestId, "empty_image", "thumbnail fetch: 0 bytes")
            }
            photoRequestId = null
        }
    }

    // -- WiFi media sync (Task 2.6a) ------------------------------------------

    /** Watchdog: importAlbum fails SILENTLY when the glasses never raise
     *  their WiFi-P2P (observed 2026-07-06: SDK retried peer discovery
     *  forever with zero listener callbacks — Lab froze at 0%). */
    private var syncWatchdog: Runnable? = null
    @Volatile private var syncActive = false

    /** Retention policy for the glasses' own photo storage (set from the app
     * Settings): 0 = keep everything, N>0 = delete synced photos older than N
     * days, -1 = purge synced photos when the glasses report storage full. */
    @Volatile private var retentionDays = 0
    /** True while a storage-FULL cleanup sync is running (retentionDays == -1),
     * so downloaded files are deleted from the glasses regardless of age. */
    @Volatile private var fullCleanupActive = false

    /** Registered once so the firmware's delete ack/err for [FileHandle.executeFileDelete]
     * surfaces in the event console (onDeletePlate = ok, onDeletePlateError = code). */
    @Volatile private var deleteAckRegistered = false

    override fun startWifiSync() {
        Log.i(TAG, "startWifiSync")
        syncActive = true
        emit(
            "syncProgress",
            mapOf("file" to "checking glasses media…", "pct" to 0, "speedKbps" to 0.0)
        )
        // Diagnostic probe first (documented 0x02/0x04 media-count command):
        // proves the BLE control channel is alive and shows what's pending.
        // Sequential — glassesControl has a single callback slot, so the
        // probe must finish before importAlbum installs its own callback.
        var proceeded = false
        fun proceed() {
            if (proceeded || !syncActive) return
            proceeded = true
            emit(
                "syncProgress",
                mapOf("file" to "WiFi-P2P pairing…", "pct" to 0, "speedKbps" to 0.0)
            )
            GlassesControl.getInstance(app)?.importAlbum()
            armSyncWatchdog()
        }
        LargeDataHandler.getInstance().glassesControl(byteArrayOf(0x02, 0x04)) { _, rsp ->
            if (rsp != null && rsp.dataType == 4) {
                emitMediaCount(rsp.imageCount, rsp.videoCount, rsp.recordCount)
                val total = rsp.imageCount + rsp.videoCount + rsp.recordCount
                if (total == 0) {
                    // Verified 2026-07-06: a 0-file importAlbum still spins up
                    // P2P, then errors out — skip the whole ceremony.
                    proceeded = true
                    syncActive = false
                    emit(
                        "syncProgress",
                        mapOf(
                            "file" to "nothing to sync — glasses empty ✓",
                            "pct" to 100,
                            "speedKbps" to 0.0,
                        )
                    )
                } else {
                    main.post { proceed() }
                }
            }
        }
        main.postDelayed({
            if (!proceeded) {
                emit(
                    "deviceEvent",
                    mapOf(
                        "hex" to "media-count probe: no reply in " +
                            "${MEDIA_COUNT_PROBE_TIMEOUT_MS} ms — trying import anyway"
                    )
                )
                proceed()
            }
        }, MEDIA_COUNT_PROBE_TIMEOUT_MS)
    }

    /** Typed count for the Media sync card (also visible in the console). */
    private fun emitMediaCount(img: Int, vid: Int, rec: Int) {
        emit(
            "mediaCount",
            mapOf("img" to img, "vid" to vid, "rec" to rec)
        )
    }

    /** Re-query the glasses' pending-media count (e.g. right after a sync). */
    private fun refreshMediaCount() {
        LargeDataHandler.getInstance().glassesControl(byteArrayOf(0x02, 0x04)) { _, rsp ->
            if (rsp != null && rsp.dataType == 4) {
                emitMediaCount(rsp.imageCount, rsp.videoCount, rsp.recordCount)
            }
        }
    }

    private fun armSyncWatchdog() {
        cancelSyncWatchdog()
        val r = Runnable {
            if (!syncActive) return@Runnable
            syncActive = false
            emit(
                "deviceEvent",
                mapOf(
                    "hex" to "WiFi sync STALLED (60 s) — glasses' WiFi-P2P never " +
                        "appeared. Try: take the glasses OFF the charger, keep them " +
                        "next to the phone, then Start sync again."
                )
            )
            // pct=100 releases the Lab's syncing spinner; the file text says why.
            emit(
                "syncProgress",
                mapOf("file" to "sync failed — see console", "pct" to 100, "speedKbps" to 0.0)
            )
        }
        syncWatchdog = r
        main.postDelayed(r, WIFI_SYNC_STALL_TIMEOUT_MS)
    }

    private fun cancelSyncWatchdog() {
        syncWatchdog?.let(main::removeCallbacks)
        syncWatchdog = null
    }

    override fun stopWifiSync() {
        cancelSyncWatchdog()
        syncActive = false
        // Verified against the .aar: the vendor exposes no cancel/stop for a
        // running importAlbum — the sync runs to completion.
        emit(
            "deviceEvent",
            mapOf("hex" to "stopWifiSync: vendor SDK has no cancel — sync runs to completion")
        )
    }

    /**
     * App-side volume (0–100 %) — Stage B's "Farry, volume badhao" path.
     * The volume block is CACHED (primed by 0x12 events and the first get)
     * so a slider release is ONE BLE write. The get path is single-shot
     * guarded: the SDK's getVolumeControl callback is persistent and re-fires
     * on every later volume packet (caused a 12-duplicate command storm,
     * 2026-07-06).
     */
    private var volCache: IntArray? = null

    private fun writeVolume(type: String, level: Int, block: IntArray) {
        fun scaled(min: Int, max: Int) =
            min + ((max - min) * level.coerceIn(0, 100)) / 100
        when (type) {
            "music" -> block[2] = scaled(block[0], block[1])
            "call" -> block[5] = scaled(block[3], block[4])
            else -> block[8] = scaled(block[6], block[7])
        }
        LargeDataHandler.getInstance().setVolumeControl(
            block[0], block[1], block[2], block[3], block[4],
            block[5], block[6], block[7], block[8], block[9],
        )
        volCache = block
        emit(
            "deviceEvent",
            mapOf(
                "hex" to "setVolume $type=$level% → music=${block[2]} " +
                    "call=${block[5]} system=${block[8]}"
            )
        )
    }

    override fun setRetentionDays(days: Int) {
        Log.i(TAG, "setRetentionDays $days")
        retentionDays = days
        // A newly-tightened policy should prune already-synced photos right away
        // (no-op if the glasses aren't connected yet — the next connect sweeps).
        sweepRetention()
    }

    /** After a photo has been synced to the phone (and exported to the gallery),
     * decide what the retention policy wants for it:
     *  - 0  keep on glasses forever
     *  - -2 delete right now ("right after sync")
     *  - -1 delete now only during a storage-full purge
     *  - N  ("older than N days") delete now if already ≥N days old, else
     *       remember it in the persistent pending queue so a LATER connect
     *       prunes it once it crosses the age line. Without this queue the
     *       day-based policy could never fire — a freshly-synced photo is
     *       always age≈0, and the firmware never re-lists it for us to re-check.
     * Safe: the file is already backed up on the phone before we ever delete. */
    private fun applyRetention(entity: com.oudmon.wifi.bean.GlassAlbumEntity) {
        val policy = retentionDays
        if (policy == 0) return // keep everything
        val name = entity.fileName ?: return
        // entity.timestamp may be seconds or millis — normalise to ms.
        var ts = entity.timestamp
        if (ts in 1..9_999_999_999L) ts *= 1000
        when {
            policy == -2 -> deleteFromGlasses(name)
            policy == -1 -> if (fullCleanupActive) deleteFromGlasses(name)
            else -> {
                val ageDays = if (ts > 0) (System.currentTimeMillis() - ts) / 86_400_000L else 0
                if (ts > 0 && ageDays >= policy) {
                    deleteFromGlasses(name)
                } else if (ts > 0) {
                    addPendingRetention(name, ts)
                    Log.i(TAG, "retention: $name kept (age ${ageDays}d < ${policy}d), queued for later")
                }
            }
        }
    }

    /** Send the glasses-side delete for one file and drop the phone-local vendor
     * cache copy. The vendor SDK exposes exactly one device-side per-file delete:
     * FileHandle.executeFileDelete(name) → BLE command 0x39 (57) with payload
     * [0x01]+UTF8(name), over the same link used to push watch-face/OTA files.
     * Fire-and-forget; the firmware confirms via onDeletePlate/onDeletePlateError
     * (surfaced by [ensureDeleteAck]). deleteFile() is local-only — it removes the
     * phone's downloaded copy, never the glasses — so we call BOTH. */
    private fun deleteFromGlasses(name: String): Boolean = try {
        ensureDeleteAck()
        FileHandle.getInstance().executeFileDelete(name)
        val localDropped = GlassesControl.getInstance(app)?.deleteFile(name) ?: false
        Log.i(TAG, "retention: sent glasses-delete for $name (local copy dropped=$localDropped)")
        emit("deviceEvent", mapOf("hex" to "retention: delete requested for $name"))
        true
    } catch (e: Exception) {
        Log.i(TAG, "retention delete failed: $e")
        false
    }

    // ---- Age-based retention queue (persisted) ------------------------------
    // Files backed up to the phone but intentionally kept on the glasses under an
    // "older than N days" policy, awaiting deletion once they age out. Persisted
    // so a photo taken today is still pruned N days later, even across restarts.
    private val retentionQueueKey = "retention_pending"

    private fun retentionPrefs() =
        app.getSharedPreferences("glasses_lab", Context.MODE_PRIVATE)

    /** name → capture-time millis. */
    private fun loadPendingRetention(): LinkedHashMap<String, Long> {
        val out = LinkedHashMap<String, Long>()
        val raw = retentionPrefs().getStringSet(retentionQueueKey, emptySet()) ?: emptySet()
        for (e in raw) {
            val i = e.lastIndexOf('|')
            if (i <= 0) continue
            val ts = e.substring(i + 1).toLongOrNull() ?: continue
            out[e.substring(0, i)] = ts
        }
        return out
    }

    private fun savePendingRetention(map: Map<String, Long>) {
        retentionPrefs().edit()
            .putStringSet(retentionQueueKey, map.entries.map { "${it.key}|${it.value}" }.toSet())
            .apply()
    }

    private fun addPendingRetention(name: String, tsMs: Long) {
        val map = loadPendingRetention()
        map[name] = tsMs
        savePendingRetention(map)
    }

    /** Prune already-backed-up photos we kept on the glasses, once they cross the
     * retention age. Runs on every (re)connect so a day-based policy fires even
     * for photos synced days ago. BLE-only — no WiFi needed. No-op unless the
     * glasses are actually connected (executeFileDelete would otherwise be lost). */
    private fun sweepRetention() {
        if (lastConnectionState != "connected") return
        val policy = retentionDays
        if (policy == 0) return // keep everything — leave the queue intact
        val pending = loadPendingRetention()
        if (pending.isEmpty()) return
        val now = System.currentTimeMillis()
        val survivors = LinkedHashMap<String, Long>()
        var removed = 0
        for ((name, ts) in pending) {
            val expired = when {
                policy == -2 -> true              // user tightened to "right after sync"
                policy == -1 -> fullCleanupActive
                else -> (now - ts) / 86_400_000L >= policy
            }
            if (expired) {
                deleteFromGlasses(name)
                removed++
            } else {
                survivors[name] = ts
            }
        }
        if (removed > 0) {
            savePendingRetention(survivors)
            Log.i(TAG, "retention sweep: deleted $removed old file(s), ${survivors.size} still waiting")
            emit("deviceEvent", mapOf("hex" to "retention sweep: removed $removed old photo(s)"))
        }
    }

    /** Register the FileHandle delete ack listener exactly once. The firmware
     * reports the result of [FileHandle.executeFileDelete] asynchronously, so
     * without this the delete would be silent and unverifiable. */
    private fun ensureDeleteAck() {
        if (deleteAckRegistered) return
        deleteAckRegistered = true
        try {
            FileHandle.getInstance().registerCallback(object : SimpleCallback() {
                override fun onDeletePlate() {
                    Log.i(TAG, "retention: glasses confirmed delete")
                    emit("deviceEvent", mapOf("hex" to "retention: glasses confirmed delete"))
                }

                override fun onDeletePlateError(code: Int) {
                    Log.i(TAG, "retention: glasses delete error code=$code")
                    emit("deviceEvent", mapOf("hex" to "retention: glasses delete error=$code"))
                }
            })
        } catch (e: Exception) {
            Log.i(TAG, "ensureDeleteAck failed: $e")
            deleteAckRegistered = false
        }
    }

    override fun setVolume(type: String, level: Int) {
        Log.i(TAG, "setVolume $type=$level")
        val cached = volCache
        if (cached != null) {
            writeVolume(type, level, cached.copyOf())
            return
        }
        var handled = false
        LargeDataHandler.getInstance().getVolumeControl { _, rsp ->
            if (handled || rsp == null) return@getVolumeControl
            handled = true
            val block = intArrayOf(
                rsp.minVolumeMusic, rsp.maxVolumeMusic, rsp.currVolumeMusic,
                rsp.minVolumeCall, rsp.maxVolumeCall, rsp.currVolumeCall,
                rsp.minVolumeSystem, rsp.maxVolumeSystem, rsp.currVolumeSystem,
                rsp.currVolumeType,
            )
            main.post { writeVolume(type, level, block) }
        }
    }

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
        // entry — safe to add up front. Remove-before-add keeps it single
        // even if this bridge is ever constructed twice in one process.
        LargeDataHandler.getInstance().removeBatteryCallBack("glasses_lab")
        LargeDataHandler.getInstance().addBatteryCallBack("glasses_lab") { _, resp ->
            if (resp != null) {
                // Battery reports only flow over a live link — if the SDK
                // reconnected silently (seen when the glasses woke on the
                // charger, 2026-07-06, with no service-discovered broadcast),
                // reflect the truth in the Lab.
                if (lastConnectionState != "connected" && !userDisconnected) {
                    cancelConnectWatchdog()
                    emitConnectionState(
                        "connected",
                        pendingMac ?: DeviceManager.getInstance().deviceAddress,
                    )
                }
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
        cancelPhotoWatchdog()
        cancelThumbnailWatchdog()
        cancelSyncWatchdog()
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
