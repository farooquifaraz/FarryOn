package com.farryon.farryon.glasses

import android.os.Handler
import android.os.Looper
import android.util.Log

/**
 * Keeps a glasses packet the vendor SDK cannot parse from killing the app.
 *
 * The SDK hands every big-data packet to a typed parser on the MAIN thread,
 * inside its own LocalBroadcastManager receiver, with no bounds checks and no
 * try/catch — and we cannot wrap a receiver the SDK registers for itself.
 * Firmware 2.20.22 on the GS4 MAX (pushed by the vendor's app, 2026-10-01)
 * started answering the volume write with a one-byte status instead of the
 * ten-value block; `VolumeControlResponse.acceptData` read index 8 of a
 * seven-byte packet and the process died — five seconds after every connect,
 * on every phone with a saved volume (S23 ×4, Vivo ×2, 2026-10-02).
 *
 * A firmware update must not be able to do that. So the main looper is
 * re-entered under a catch: an exception that came up through
 * `LocalBroadcastManager.executePendingBroadcasts` — which in this app means
 * glasses plumbing and nothing else — is logged, reported, and the loop
 * carries on with that one packet dropped. Anything else is rethrown and
 * crashes exactly as before: this is not a licence to swallow real bugs.
 */
object VendorCrashGuard {
    private const val TAG = "GlassesLab"
    private const val LBM = "androidx.localbroadcastmanager.content.LocalBroadcastManager"

    @Volatile
    private var installed = false

    /** Told about each swallowed exception, on the main thread. */
    @Volatile
    var onSwallowed: ((Throwable) -> Unit)? = null

    /** True when [t] was thrown while a local broadcast was being delivered. */
    fun cameThroughLocalBroadcast(t: Throwable): Boolean =
        t is RuntimeException && t.stackTrace.any {
            it.className == LBM && it.methodName == "executePendingBroadcasts"
        }

    /** Once per process; safe to call again. */
    fun install() {
        if (installed) return
        installed = true
        Handler(Looper.getMainLooper()).post {
            while (true) {
                try {
                    Looper.loop()
                    return@post // the main looper quit: the process is ending
                } catch (t: Throwable) {
                    if (!cameThroughLocalBroadcast(t)) throw t
                    Log.e(TAG, "vendor packet parser threw — packet dropped, app kept alive", t)
                    try {
                        onSwallowed?.invoke(t)
                    } catch (e: Throwable) {
                        Log.e(TAG, "crash-guard listener threw: $e")
                    }
                }
            }
        }
    }
}
