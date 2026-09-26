package com.farryon.farryon.glasses

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ThumbnailAssemblerTest {

    /** A chunk as the glasses send it: bc fd len crc 01 total index payload. */
    private fun chunk(total: Int, index: Int, payload: ByteArray): ByteArray {
        val head = byteArrayOf(
            0xbc.toByte(), 0xfd.toByte(), 0, 0, 0, 0, 0x01,
            (total and 0xff).toByte(), (total shr 8).toByte(),
            (index and 0xff).toByte(), (index shr 8).toByte(),
        )
        return head + payload
    }

    private fun payload(i: Int) = byteArrayOf(i.toByte(), (i + 100).toByte())

    @Test
    fun `chunks in order build the picture and ask for each next one once`() {
        val a = ThumbnailAssembler()
        assertEquals(ThumbnailAssembler.Step.Next(1), a.accept(chunk(3, 0, payload(0))))
        assertEquals(ThumbnailAssembler.Step.Next(2), a.accept(chunk(3, 1, payload(1))))
        val done = a.accept(chunk(3, 2, payload(2)))
        assertTrue(done is ThumbnailAssembler.Step.Done)
        assertArrayEquals(payload(0) + payload(1) + payload(2), (done as ThumbnailAssembler.Step.Done).jpeg)
        assertEquals(0, a.duplicates)
    }

    @Test
    fun `a repeated chunk is dropped and asks for nothing (GS5 MAX 2026-09-25)`() {
        val a = ThumbnailAssembler()
        assertEquals(ThumbnailAssembler.Step.Next(1), a.accept(chunk(3, 0, payload(0))))
        // The glasses send chunk 0 again: the SDK's loop would ask for chunk 1 twice.
        assertEquals(ThumbnailAssembler.Step.Duplicate, a.accept(chunk(3, 0, payload(0))))
        assertEquals(ThumbnailAssembler.Step.Next(2), a.accept(chunk(3, 1, payload(1))))
        assertEquals(ThumbnailAssembler.Step.Duplicate, a.accept(chunk(3, 1, payload(1))))
        val done = a.accept(chunk(3, 2, payload(2))) as ThumbnailAssembler.Step.Done
        // The picture holds each chunk once.
        assertArrayEquals(payload(0) + payload(1) + payload(2), done.jpeg)
        assertEquals(2, a.duplicates)
    }

    @Test
    fun `a single-chunk picture is done at once`() {
        val a = ThumbnailAssembler()
        val done = a.accept(chunk(1, 0, payload(7)))
        assertArrayEquals(payload(7), (done as ThumbnailAssembler.Step.Done).jpeg)
    }

    @Test
    fun `total zero is the glasses giving up`() {
        val a = ThumbnailAssembler()
        a.accept(chunk(3, 0, payload(0)))
        // bc fd 05 00 19 c0 01 00 00 00 00 — seen on GS5 MAX mid-transfer.
        val abort = byteArrayOf(
            0xbc.toByte(), 0xfd.toByte(), 0x05, 0x00, 0x19, 0xc0.toByte(), 0x01, 0, 0, 0, 0,
        )
        assertEquals(ThumbnailAssembler.Step.Aborted, a.accept(abort))
        // Nothing was lost: the next real chunk still fits.
        assertEquals(ThumbnailAssembler.Step.Next(2), a.accept(chunk(3, 1, payload(1))))
    }

    @Test
    fun `a chunk from a different picture is never spliced in`() {
        val a = ThumbnailAssembler()
        a.accept(chunk(3, 0, payload(0)))
        assertEquals(ThumbnailAssembler.Step.Duplicate, a.accept(chunk(5, 1, payload(9))))
        assertEquals(1, a.expected)
    }

    @Test
    fun `a skipped-ahead chunk is not taken`() {
        val a = ThumbnailAssembler()
        a.accept(chunk(4, 0, payload(0)))
        assertEquals(ThumbnailAssembler.Step.Duplicate, a.accept(chunk(4, 2, payload(2))))
        assertEquals(1, a.expected)
    }

    @Test
    fun `short or foreign packets are malformed, not data`() {
        val a = ThumbnailAssembler()
        assertEquals(ThumbnailAssembler.Step.Malformed, a.accept(null))
        assertEquals(ThumbnailAssembler.Step.Malformed, a.accept(byteArrayOf(0xbc.toByte(), 0xfd.toByte())))
        val otherCmd = chunk(3, 0, payload(0)).also { it[1] = 0x41 }
        assertEquals(ThumbnailAssembler.Step.Malformed, a.accept(otherCmd))
        assertEquals(0, a.bytes)
    }

    @Test
    fun `index and total are little-endian 16-bit`() {
        val a = ThumbnailAssembler()
        // 300 chunks: total bytes 2c 01.
        assertEquals(ThumbnailAssembler.Step.Next(1), a.accept(chunk(300, 0, payload(0))))
        assertEquals(300, a.total)
    }
}
