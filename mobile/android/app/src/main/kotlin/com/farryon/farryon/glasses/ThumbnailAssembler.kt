package com.farryon.farryon.glasses

import java.io.ByteArrayOutputStream

/**
 * Rebuilds the AI-photo thumbnail from the glasses' numbered BLE chunks,
 * taking each chunk exactly once.
 *
 * Wire format (vendor cmd 0xFD, read from the SDK's own handler):
 * `bc fd <len:2> <crc:2> 01 <total:2 LE> <index:2 LE> <jpeg bytes…>`.
 * The app asks for chunk N, the glasses answer with chunk N, the app asks for
 * N+1 — one request per chunk.
 *
 * Why this exists: the vendor SDK asks for the next chunk every time ANY
 * chunk arrives. GS5 MAX (2026-09-25) sent chunk 0 twice; the SDK asked for
 * chunk 1 twice, got it twice, asked for chunk 2 four times… until the
 * glasses gave up with `total = 0`. Here a repeat of a chunk already taken is
 * dropped and asks for nothing, so one stray duplicate stays one duplicate.
 *
 * Pure logic, no Android or SDK types: the caller does the asking.
 */
internal class ThumbnailAssembler {

    sealed class Step {
        /** Chunk taken; ask the glasses for [index] next. */
        data class Next(val index: Int) : Step()

        /** Last chunk taken; [jpeg] is the whole picture. */
        class Done(val jpeg: ByteArray) : Step()

        /** A chunk already taken (or not the one asked for): drop it, ask nothing. */
        object Duplicate : Step()

        /** The glasses answered "nothing to send" (total = 0): the transfer is over on their side. */
        object Aborted : Step()

        /** Not a thumbnail packet at all. */
        object Malformed : Step()
    }

    private val buffer = ByteArrayOutputStream()

    /** The chunk index the next accepted packet must carry. */
    var expected = 0
        private set

    /** Chunk count announced by the first chunk (0 until then). */
    var total = 0
        private set

    /** Packets dropped as repeats — logged, so a doubling firmware is visible. */
    var duplicates = 0
        private set

    val bytes: Int get() = buffer.size()

    fun accept(packet: ByteArray?): Step {
        if (packet == null || packet.size < HEADER) return Step.Malformed
        if (packet[0].toInt() and 0xff != 0xbc || packet[1].toInt() and 0xff != 0xfd) {
            return Step.Malformed
        }
        val total = u16(packet, 7)
        val index = u16(packet, 9)
        if (total <= 0) return Step.Aborted
        // A different announced total means a different picture's stream —
        // never splice it into this one.
        if (index != expected || (this.total != 0 && total != this.total)) {
            duplicates++
            return Step.Duplicate
        }
        this.total = total
        buffer.write(packet, HEADER, packet.size - HEADER)
        expected = index + 1
        return if (expected >= total) Step.Done(buffer.toByteArray()) else Step.Next(expected)
    }

    companion object {
        /** bc fd len(2) crc(2) 01 total(2) index(2). */
        const val HEADER = 11

        private fun u16(b: ByteArray, off: Int): Int =
            (b[off].toInt() and 0xff) or ((b[off + 1].toInt() and 0xff) shl 8)
    }
}
