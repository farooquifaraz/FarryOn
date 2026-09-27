package com.farryon.farryon

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.File

/**
 * Hands a downloaded APK to Android's package installer.
 *
 * The app is sideloaded, so an update is an APK the app fetched itself
 * (core/app_installer.dart, into its cache) — this opens the system
 * "Install" sheet for it. Going through the browser instead stalled at 100%
 * on a Vivo (2026-09-27: Chrome kept the finished file as ".pending" and
 * never offered Install). Android still asks the user to confirm every
 * install; nothing here installs silently.
 *
 * Android 8+ gates this behind the per-app "Install unknown apps" setting:
 * [canInstall] says whether it is granted, [openInstallSettings] opens the
 * page where the user grants it (the app is brought back afterwards).
 */
class InstallChannel(private val app: Context) : MethodChannel.MethodCallHandler {
    companion object {
        private const val TAG = "InstallChannel"
        private const val CHANNEL = "com.farryon/install"
        private const val APK_MIME = "application/vnd.android.package-archive"

        fun register(messenger: BinaryMessenger, app: Context): InstallChannel {
            val handler = InstallChannel(app)
            MethodChannel(messenger, CHANNEL).setMethodCallHandler(handler)
            return handler
        }
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "canInstall" -> result.success(canInstall())
            "openInstallSettings" -> result.success(openInstallSettings())
            "openInstaller" -> {
                val path = call.argument<String>("path")
                if (path.isNullOrEmpty()) {
                    result.error("no_path", "apk path is required", null)
                    return
                }
                try {
                    openInstaller(File(path))
                    result.success(true)
                } catch (e: Exception) {
                    Log.w(TAG, "openInstaller failed", e)
                    result.error("install_failed", e.message ?: e.toString(), null)
                }
            }
            else -> result.notImplemented()
        }
    }

    private fun canInstall(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.O ||
            app.packageManager.canRequestPackageInstalls()

    private fun openInstallSettings(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return true
        return try {
            app.startActivity(
                Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:${app.packageName}"),
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
            true
        } catch (e: Exception) {
            Log.w(TAG, "install settings failed", e)
            false
        }
    }

    private fun openInstaller(apk: File) {
        require(apk.isFile && apk.length() > 0L) { "no such apk: ${apk.path}" }
        // A content:// grant, never a file:// path: file URIs have been refused
        // by the installer since Android 7.
        val uri = FileProvider.getUriForFile(app, "${app.packageName}.fileprovider", apk)
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, APK_MIME)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_GRANT_READ_URI_PERMISSION)
        app.startActivity(intent)
    }
}
