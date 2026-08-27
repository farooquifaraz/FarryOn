package com.farryon.farryon

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.util.Log
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * Saves in-memory image bytes (a phone-camera or smart-glasses capture) into
 * the user's photo gallery, under `Pictures/Farry`.
 *
 * The glasses SDK already exports WiFi-synced FILES to the gallery
 * (HeyCyanGlassesSdk.exportToGallery); this channel is the provider-agnostic
 * BYTES path for the live "what is this" captures the Flutter layer holds in
 * memory (never touched disk otherwise). Uses scoped-storage MediaStore, so no
 * runtime permission is needed on Android 10+ (the only targets here).
 */
class MediaChannel(private val app: Context) : MethodChannel.MethodCallHandler {
    companion object {
        private const val TAG = "MediaChannel"
        private const val CHANNEL = "com.farryon/media"

        fun register(messenger: BinaryMessenger, app: Context): MediaChannel {
            val handler = MediaChannel(app)
            MethodChannel(messenger, CHANNEL).setMethodCallHandler(handler)
            return handler
        }
    }

    private val main = Handler(Looper.getMainLooper())

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "saveImageToGallery" -> {
                val bytes = call.argument<ByteArray>("bytes")
                val name = call.argument<String>("name")
                    ?: "Farry_${System.currentTimeMillis()}.jpg"
                if (bytes == null || bytes.isEmpty()) {
                    result.error("no_bytes", "image bytes are required", null)
                    return
                }
                // Off the platform thread: the MediaStore write is blocking I/O.
                Thread { saveJpeg(bytes, name, result) }.start()
            }
            "saveVideoToGallery" -> {
                val path = call.argument<String>("path")
                val name = call.argument<String>("name")
                    ?: "Farry_${System.currentTimeMillis()}.mp4"
                if (path.isNullOrEmpty()) {
                    result.error("no_path", "video path is required", null)
                    return
                }
                // A recording is tens/hundreds of MB — stream-copy it off the
                // platform thread, then delete the temp file.
                Thread { saveVideo(path, name, result) }.start()
            }
            else -> result.notImplemented()
        }
    }

    private fun saveVideo(path: String, name: String, result: MethodChannel.Result) {
        try {
            val src = java.io.File(path)
            if (!src.exists() || src.length() == 0L) {
                main.post { result.error("no_file", "recording file missing/empty", null) }
                return
            }
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                put(MediaStore.MediaColumns.MIME_TYPE, "video/mp4")
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    put(MediaStore.MediaColumns.RELATIVE_PATH, "Movies/Farry")
                    put(MediaStore.MediaColumns.IS_PENDING, 1)
                }
            }
            val uri = app.contentResolver.insert(
                MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values,
            )
            if (uri == null) {
                main.post { result.error("insert_failed", "MediaStore insert returned null", null) }
                return
            }
            app.contentResolver.openOutputStream(uri)?.use { out ->
                src.inputStream().use { it.copyTo(out) }
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                app.contentResolver.update(
                    uri,
                    ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) },
                    null, null,
                )
            }
            src.delete() // the temp copy has served its purpose
            Log.i(TAG, "saved $name to gallery (${src.length()} bytes)")
            main.post { result.success(uri.toString()) }
        } catch (e: Exception) {
            Log.i(TAG, "video save failed: $e")
            main.post { result.error("save_failed", e.message, null) }
        }
    }

    private fun saveJpeg(bytes: ByteArray, name: String, result: MethodChannel.Result) {
        try {
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                put(MediaStore.MediaColumns.MIME_TYPE, "image/jpeg")
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    put(MediaStore.MediaColumns.RELATIVE_PATH, "Pictures/Farry")
                    put(MediaStore.MediaColumns.IS_PENDING, 1)
                }
            }
            val uri = app.contentResolver.insert(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values,
            )
            if (uri == null) {
                main.post { result.error("insert_failed", "MediaStore insert returned null", null) }
                return
            }
            app.contentResolver.openOutputStream(uri)?.use { it.write(bytes) }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                // Clear IS_PENDING so the image becomes visible in the gallery.
                app.contentResolver.update(
                    uri,
                    ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) },
                    null, null,
                )
            }
            Log.i(TAG, "saved $name to gallery")
            main.post { result.success(uri.toString()) }
        } catch (e: Exception) {
            Log.i(TAG, "save failed: $e")
            main.post { result.error("save_failed", e.message, null) }
        }
    }
}
