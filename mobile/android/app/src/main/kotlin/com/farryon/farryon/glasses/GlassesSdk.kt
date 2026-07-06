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
    fun pairClassicBt()
    fun startAudioTest(mode: String)
    fun stopAudioTest()
    fun startWifiSync()
    fun stopWifiSync()
    fun setVolume(type: String, level: Int)
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

    override fun dispose() {
        stopAudioTest()
        stopWifiSync()
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
