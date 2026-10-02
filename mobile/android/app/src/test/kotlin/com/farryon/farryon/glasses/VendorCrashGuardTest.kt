package com.farryon.farryon.glasses

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Which exceptions the main-looper guard may swallow. The line matters: too
 * narrow and a firmware update kills the app again (GS4 MAX fw 2.20.22,
 * 2026-10-02); too wide and a real bug of ours is silently eaten.
 */
class VendorCrashGuardTest {
    private fun frame(cls: String, method: String) =
        StackTraceElement(cls, method, "SourceFile", 1)

    private val lbm = "androidx.localbroadcastmanager.content.LocalBroadcastManager"

    @Test
    fun `the crash from the device log is recognised`() {
        // Exactly the trace from the S23 and the Vivo: R8 names for
        // VolumeControlResponse / LargeDataParser / the big-data receiver.
        val t = ArrayIndexOutOfBoundsException("length=7; index=8").apply {
            stackTrace = arrayOf(
                frame("J0.n", "b"),
                frame("G0.h", "a"),
                frame("G0.i", "onReceive"),
                frame(lbm, "executePendingBroadcasts"),
                frame("$lbm\$1", "handleMessage"),
                frame("android.os.Handler", "dispatchMessage"),
                frame("android.os.Looper", "loop"),
            )
        }
        assertTrue(VendorCrashGuard.cameThroughLocalBroadcast(t))
    }

    @Test
    fun `an exception from anywhere else still crashes`() {
        val t = IllegalStateException("a real bug of ours").apply {
            stackTrace = arrayOf(
                frame("com.farryon.farryon.MainActivity", "onCreate"),
                frame("android.os.Handler", "dispatchMessage"),
                frame("android.os.Looper", "loop"),
            )
        }
        assertFalse(VendorCrashGuard.cameThroughLocalBroadcast(t))
    }

    @Test
    fun `an Error is never swallowed, wherever it came from`() {
        val t = OutOfMemoryError().apply {
            stackTrace = arrayOf(frame(lbm, "executePendingBroadcasts"))
        }
        assertFalse(VendorCrashGuard.cameThroughLocalBroadcast(t))
    }
}
