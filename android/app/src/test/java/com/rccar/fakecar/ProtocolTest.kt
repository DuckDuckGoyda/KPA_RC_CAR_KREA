package com.rccar.fakecar

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

fun hex(s: String): ByteArray = Protocol.parseHex(s)!!

class ProtocolTest {

    // ---- test vectors from the spec ----

    @Test fun crcCheckValue() {
        assertEquals(0xA1, Protocol.crc8("123456789".toByteArray(Charsets.US_ASCII)))
    }

    @Test fun getTelCommand() {
        val p = Protocol.buildPacket(0, 1, byteArrayOf(Protocol.GET_TEL.toByte()))
        assertEquals("AC 53 04 00 01 04 AB", Protocol.hex(p))
    }

    @Test fun ssSaCommand() {
        val p = Protocol.buildPacket(0, 1, byteArrayOf(Protocol.SS_SA.toByte(), 50, -20))
        assertEquals("AC 53 06 00 01 03 32 EC 2F", Protocol.hex(p))
    }

    @Test fun ackReply() {
        assertEquals("AC 53 04 00 01 00 CA", Protocol.hex(Protocol.reply(0, Protocol.ACK_OK)))
    }

    // ---- parser ----

    private fun packets(events: List<PacketParser.Event>) =
        events.filterIsInstance<PacketParser.Event.Packet>().map { Protocol.hex(it.frame.bytes) }

    @Test fun parsesWholePacket() {
        val events = PacketParser().feed(hex("AC 53 06 05 01 03 32 EC 2F"), 0)
        val frame = (events.single() as PacketParser.Event.Packet).frame
        assertEquals(6, frame.len)
        assertEquals(5, frame.sqn)
        assertEquals(1, frame.addr)
        assertArrayEquals(hex("03 32 EC"), frame.payload)
    }

    @Test fun skipsJunkBeforeSync() {
        val events = PacketParser().feed(hex("00 FF 53 AC 12 AC 53 04 00 01 04 AB"), 0)
        val junk = events[0] as PacketParser.Event.Junk
        assertEquals("00 FF 53 AC 12", Protocol.hex(junk.bytes))
        assertEquals(listOf("AC 53 04 00 01 04 AB"), packets(events))
    }

    @Test fun resyncsAfterBadLen() {
        val parser = PacketParser()
        // LEN 02 (too small) and LEN FE (too big) are dropped, then a real packet follows
        assertEquals(
            listOf("AC 53 04 00 01 04 AB"),
            packets(parser.feed(hex("AC 53 02 AC 53 FE AC 53 04 00 01 04 AB"), 0))
        )
    }

    @Test fun falseSyncWithPlausibleLenIsClearedByTimeout() {
        val parser = PacketParser()
        // LEN = 0xAC (172) is in range, so the parser waits for 175 bytes that never come...
        assertTrue(packets(parser.feed(hex("AC 53 AC 53 04 00 01 04 AB"), 0)).isEmpty())
        // ...until the 100 ms timeout drops them; the next packet is parsed normally.
        assertEquals(listOf("AC 53 04 00 01 04 AB"), packets(parser.feed(hex("AC 53 04 00 01 04 AB"), 200)))
    }

    @Test fun joinsPacketSplitAcrossReads() {
        val parser = PacketParser()
        val bytes = hex("AC 53 06 00 01 03 32 EC 2F")
        for (i in 0 until bytes.size - 1) {
            assertTrue(packets(parser.feed(byteArrayOf(bytes[i]), i.toLong())).isEmpty())
        }
        assertEquals(listOf("AC 53 06 00 01 03 32 EC 2F"), packets(parser.feed(byteArrayOf(bytes.last()), 50)))
    }

    @Test fun twoPacketsInOneRead() {
        val events = PacketParser().feed(hex("AC 53 04 00 01 04 AB AC 53 04 01 01 04 00"), 0)
        assertEquals(2, packets(events).size)
    }

    @Test fun trailingSyncByteIsKept() {
        val parser = PacketParser()
        assertTrue(packets(parser.feed(hex("11 22 AC"), 0)).isEmpty())
        assertEquals(listOf("AC 53 04 00 01 04 AB"), packets(parser.feed(hex("53 04 00 01 04 AB"), 10)))
    }

    @Test fun dropsPartialPacketAfterTimeout() {
        val parser = PacketParser()
        parser.feed(hex("AC 53 06 00 01"), 0)
        val events = parser.feed(hex("03 32 EC 2F"), 150) // too late
        assertTrue(packets(events).isEmpty())
        assertTrue(events.first() is PacketParser.Event.Junk)
        // and the parser still works afterwards
        assertEquals(1, packets(parser.feed(hex("AC 53 04 00 01 04 AB"), 160)).size)
    }

    @Test fun keepsPartialPacketWithinTimeout() {
        val parser = PacketParser()
        parser.feed(hex("AC 53 06 00 01"), 0)
        assertEquals(1, packets(parser.feed(hex("03 32 EC 2F"), 90)).size)
    }

    @Test fun badCrcIsDetected() {
        val frame = (PacketParser().feed(hex("AC 53 04 07 01 04 00"), 0).single() as PacketParser.Event.Packet).frame
        assertEquals(false, frame.crcOk)
        assertEquals(7, frame.sqn)
    }

    // ---- helpers ----

    @Test fun parseHexFormats() {
        assertArrayEquals(hex("AC 53 04"), Protocol.parseHex("ac5304"))
        assertArrayEquals(hex("AC 53 04"), Protocol.parseHex("0xAC, 0x53, 0x04"))
        assertNull(Protocol.parseHex("AC 5"))
        assertNull(Protocol.parseHex("GG"))
        assertNull(Protocol.parseHex(""))
    }

    @Test fun describesCommands() {
        val f = Frame(Protocol.buildPacket(5, 1, hex("03 32 EC")))
        assertEquals("SS_SA speed=50 angle=-20  (sqn 5)", Protocol.describeCommand(f))
    }
}
