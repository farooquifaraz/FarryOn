package com.farryon.farryon.glasses

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Handler
import android.os.Looper
import java.io.ByteArrayOutputStream

/**
 * Everything the Glasses Lab (and later GlassesCaptureSource) needs from a
 * glasses vendor SDK, expressed vendor-neutrally.
 *
 * Two implementations:
 *  - [StubGlassesSdk] (below): simulates an L801 so the Lab UI, the Dart
 *    bridge and the event pipeline are fully exercisable on any phone or
 *    emulator with NO vendor .aar and NO Bluetooth permissions.
 *  - `HeyCyanGlassesSdk` (Sprint 2, written on the developer machine that has
 *    the vendor .aar in `app/libs/`). Mapping from this interface to the
 *    HeyCyan SDK (see GLASSES_SDK_DOC_EN.pdf + GlassesSDKSample):
 *      scan()            -> BleScannerHelper.scanDevice()
 *      connect()         -> BleOperateManager.connectDirectly(mac)
 *      setAutoReconnect  -> BleOperateManager.setNeedConnect(...)
 *      requestBattery    -> LargeDataHandler.syncBattery() (+ event 0x05)
 *      requestDeviceInfo -> LargeDataHandler.syncDeviceInfo()
 *      takePhoto         -> glassesControl(byteArrayOf(0x02, 0x01, 0x01))
 *      takeAiPhoto       -> glassesControl AI-recognition variant (0x06)
 *                           + getPictureThumbnails() -> "thumbnail" event
 *      pairClassicBt     -> classicBluetoothStartScan() +
 *                           createBondBluetoothJieLi(device)
 *      startAudioTest("pcm") -> voiceFromGlasses(pcm) callbacks
 *                               -> "pcmChunk" events
 *      startWifiSync     -> GlassesControl.importAlbum() +
 *                           WifiFilesDownloadListener -> "syncProgress" events
 *      setVolume         -> getVolumeControl() / volume commands
 *      device events     -> GlassesDeviceNotifyListener.parseData()
 *                           -> "wearState" / "gesture" / "deviceEvent" events
 */
interface GlassesSdk {
    /** `stub` or `heycyan` — surfaced in the Lab's banner. */
    val implementationName: String
    val sdkVersion: String

    /** All device data flows through this single listener as (type, data). */
    fun setListener(listener: GlassesSdkListener?)

    fun scan(timeoutMs: Long, onResult: (List<Map<String, Any?>>) -> Unit)
    fun connect(mac: String)
    fun disconnect()
    fun setAutoReconnect(enabled: Boolean)
    fun requestBattery()
    fun requestDeviceInfo()
    fun takePhoto()
    fun takeAiPhoto(requestId: String)

    /**
     * Record video ON THE GLASSES for [seconds]. Lifecycle is reported as
     * `videoState` events (`recording` / `stopped` / `failed`), never as a
     * return value — the recording outlives the call by minutes.
     *
     * The duration the user picked is enforced by the FIRMWARE (see
     * [setVideoDuration]); the app only mirrors it for the UI clock. The wire
     * protocol and why it must work that way are in
     * `mobile/lib/features/glasses_lab/LAB_NOTES.md` ("Video recording").
     */
    fun startVideoRecording(requestId: String, seconds: Int)

    /** Stop a recording early. No-op (with a `videoState` report) if idle. */
    fun stopVideoRecording()

    /**
     * Persist the recording length and push it to the glasses (now if they're
     * connected, otherwise on the next connect). Separate from
     * [startVideoRecording] on purpose: the firmware auto-stops at its OWN
     * configured duration, so that value has to be correct BEFORE a recording
     * starts — exactly how the vendor app does it.
     */
    fun setVideoDuration(seconds: Int)
    fun pairClassicBt()
    fun startAudioTest(mode: String)
    fun stopAudioTest()
    fun startWifiSync()
    fun stopWifiSync()
    fun setVolume(type: String, level: Int)

    /** Auto-delete synced glasses photos to free the headset's storage.
     * `0` = never, `1/7/30` = delete photos older than that many days after
     * they sync to the phone, `-1` = only when the glasses report storage full. */
    fun setRetentionDays(days: Int)

    fun dispose()
}

fun interface GlassesSdkListener {
    /** Always invoked on the main thread. */
    fun onEvent(type: String, data: Map<String, Any?>)
}

/**
 * Simulated L801. Timings roughly follow the real device (connect < 1 s,
 * thumbnail ~1.5 s) so the Lab's latency readouts look realistic, and the
 * banner says STUB MODE so simulated numbers are never mistaken for hardware
 * truth.
 */
class StubGlassesSdk : GlassesSdk {
    override val implementationName = "stub"
    override val sdkVersion = "sim-1.0"

    private val main = Handler(Looper.getMainLooper())
    private var listener: GlassesSdkListener? = null
    private var connected = false
    private var battery = 87
    private var audioTask: Runnable? = null
    private var syncTask: Runnable? = null

    private fun emit(type: String, data: Map<String, Any?>) {
        main.post { listener?.onEvent(type, data) }
    }

    override fun setListener(listener: GlassesSdkListener?) {
        this.listener = listener
    }

    override fun scan(timeoutMs: Long, onResult: (List<Map<String, Any?>>) -> Unit) {
        main.postDelayed({
            onResult(
                listOf(
                    mapOf("name" to "L801-STUB", "mac" to "AA:BB:CC:DD:EE:FF", "rssi" to -48),
                )
            )
        }, minOf(1200L, timeoutMs))
    }

    override fun connect(mac: String) {
        main.postDelayed({
            connected = true
            emit("connectionState", mapOf("state" to "connected", "mac" to mac))
            emit("battery", mapOf("pct" to battery, "charging" to false))
            emit("wearState", mapOf("worn" to true))
        }, 800L)
    }

    override fun disconnect() {
        connected = false
        stopAudioTest()
        stopWifiSync()
        if (videoRequestId != null) finishVideo("disconnected")
        emit("connectionState", mapOf("state" to "disconnected"))
    }

    override fun setAutoReconnect(enabled: Boolean) {
        emit("deviceEvent", mapOf("hex" to "autoReconnect=$enabled"))
    }

    override fun requestBattery() {
        battery = maxOf(5, battery - 1)
        emit("battery", mapOf("pct" to battery, "charging" to false))
    }

    override fun requestDeviceInfo() {
        emit(
            "deviceInfo",
            mapOf(
                "btFirmware" to "STUB-BT-1.0.2",
                "wifiFirmware" to "STUB-WIFI-0.9.1",
                "hardware" to "L801 (simulated)",
            )
        )
    }

    override fun takePhoto() {
        emit("deviceEvent", mapOf("hex" to "photo_stored_on_glasses"))
    }

    override fun takeAiPhoto(requestId: String) {
        val started = android.os.SystemClock.elapsedRealtime()
        main.postDelayed({
            val elapsed = android.os.SystemClock.elapsedRealtime() - started
            emit(
                "thumbnail",
                mapOf(
                    "requestId" to requestId,
                    "jpeg" to fakeJpeg(),
                    "elapsedMs" to elapsed.toInt(),
                )
            )
        }, 1500L)
    }

    /** Simulated recording: one pending auto-stop, mirroring the real bridge's
     *  single-in-flight rule so the Lab/UI exercises the same states. */
    private var videoStop: Runnable? = null
    private var videoRequestId: String? = null
    private var videoSeconds = 0
    private var videoStartedAt = 0L

    override fun startVideoRecording(requestId: String, seconds: Int) {
        if (videoRequestId != null) {
            emit(
                "videoState",
                mapOf(
                    "state" to "failed", "requestId" to requestId,
                    "seconds" to seconds, "elapsedMs" to 0, "reason" to "busy",
                    "detail" to "a recording ($videoRequestId) is already running",
                )
            )
            return
        }
        if (!connected) {
            emit(
                "videoState",
                mapOf(
                    "state" to "failed", "requestId" to requestId,
                    "seconds" to seconds, "elapsedMs" to 0,
                    "reason" to "not_connected",
                    "detail" to "the glasses aren't connected (simulated)",
                )
            )
            return
        }
        videoRequestId = requestId
        videoSeconds = seconds
        videoStartedAt = android.os.SystemClock.elapsedRealtime()
        emit(
            "videoState",
            mapOf(
                "state" to "recording", "requestId" to requestId,
                "seconds" to seconds, "elapsedMs" to 0,
            )
        )
        val stop = Runnable { finishVideo("duration_reached") }
        videoStop = stop
        main.postDelayed(stop, seconds * 1000L)
    }

    override fun stopVideoRecording() {
        if (videoRequestId == null) return
        finishVideo("user_stopped")
    }

    private fun finishVideo(reason: String) {
        val id = videoRequestId ?: return
        videoStop?.let(main::removeCallbacks)
        videoStop = null
        videoRequestId = null
        emit(
            "videoState",
            mapOf(
                "state" to "stopped", "requestId" to id,
                "seconds" to videoSeconds,
                "elapsedMs" to
                    (android.os.SystemClock.elapsedRealtime() - videoStartedAt).toInt(),
                "reason" to reason,
            )
        )
        emit("mediaCount", mapOf("img" to 0, "vid" to 1, "rec" to 0))
    }

    override fun setVideoDuration(seconds: Int) {
        emit("deviceEvent", mapOf("hex" to "videoDuration=${seconds}s (stub)"))
    }

    override fun pairClassicBt() {
        emit("audio", mapOf("status" to "classic_bt_bonded (simulated)"))
    }

    override fun startAudioTest(mode: String) {
        when (mode) {
            "pcm" -> {
                // ~10 chunks/sec of 320-byte "PCM" — enough to exercise the
                // event pipeline and the Lab's chunk/rate readout.
                val tick = object : Runnable {
                    override fun run() {
                        emit(
                            "pcmChunk",
                            mapOf(
                                "bytes" to 320,
                                "sampleRate" to 16000,
                                "data" to ByteArray(320),
                            )
                        )
                        main.postDelayed(this, 100L)
                    }
                }
                audioTask = tick
                main.post(tick)
            }
            "hfp" -> emit("audio", mapOf("status" to "hfp_route_active (simulated)"))
            "tts" -> emit("audio", mapOf("status" to "tts_played_on_glasses (simulated)"))
            "wake" -> emit("audio", mapOf("status" to "aiVoiceWake_ack (simulated)"))
        }
    }

    override fun stopAudioTest() {
        audioTask?.let(main::removeCallbacks)
        audioTask = null
        emit("audio", mapOf("status" to "audio_test_stopped"))
    }

    override fun startWifiSync() {
        emit("mediaCount", mapOf("img" to 1, "vid" to 0, "rec" to 0))
        var pct = 0
        val tick = object : Runnable {
            override fun run() {
                pct = minOf(100, pct + 10)
                emit(
                    "syncProgress",
                    mapOf(
                        "file" to "IMG_20260705_001.jpg",
                        "pct" to pct,
                        "speedKbps" to 420.0,
                    )
                )
                if (pct < 100) {
                    main.postDelayed(this, 300L)
                } else {
                    emit("mediaCount", mapOf("img" to 0, "vid" to 0, "rec" to 0))
                }
            }
        }
        syncTask = tick
        main.post(tick)
    }

    override fun stopWifiSync() {
        syncTask?.let(main::removeCallbacks)
        syncTask = null
    }

    override fun setVolume(type: String, level: Int) {
        emit("deviceEvent", mapOf("hex" to "volume:$type=$level"))
    }

    override fun setRetentionDays(days: Int) {
        emit("deviceEvent", mapOf("hex" to "retentionDays=$days (stub)"))
    }

    override fun dispose() {
        stopAudioTest()
        stopWifiSync()
        videoStop?.let(main::removeCallbacks)
        videoStop = null
        videoRequestId = null
        listener = null
    }

    /** A generated 320×240 JPEG so Image.memory has something real to render. */
    private fun fakeJpeg(): ByteArray {
        val bmp = Bitmap.createBitmap(320, 240, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bmp)
        canvas.drawColor(Color.rgb(14, 36, 43))
        val paint = Paint().apply {
            color = Color.rgb(93, 202, 165)
            textSize = 28f
            isAntiAlias = true
        }
        canvas.drawText("L801 STUB FRAME", 40f, 120f, paint)
        paint.textSize = 16f
        canvas.drawText(System.currentTimeMillis().toString(), 40f, 150f, paint)
        val out = ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.JPEG, 80, out)
        bmp.recycle()
        return out.toByteArray()
    }
}
