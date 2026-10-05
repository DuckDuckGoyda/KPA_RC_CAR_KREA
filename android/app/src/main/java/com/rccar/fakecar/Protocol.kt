package com.rccar.fakecar

import java.io.ByteArrayOutputStream

/**
 * Packet format (both directions):
 *
 *     AC 53 | LEN | SQN | ADDR | PAYLOAD... | CRC
 *
 * LEN counts the bytes after itself (SQN..CRC), so a full packet is LEN + 3 bytes.
 * CRC is CRC-8/MAXIM-DOW over LEN..last payload byte.
 * Command payload = CODE + params, reply payload = ACK + data.
 */
object Protocol {
    const val SYNC1 = 0xAC
    const val SYNC2 = 0x53
    const val ADDR = 1
    const val MIN_LEN = 4
    const val MAX_LEN = 253

    // Command codes
    const val SET_SPEED = 0x01
    const val SET_ANGLE = 0x02
    const val SS_SA = 0x03
    const val GET_TEL = 0x04
    const val SS_SA_GT = 0x07
    const val LED = 0xFE
    const val VERSION = 0xFF

    // ACK codes
    const val ACK_OK = 0
    const val ACK_BAD_ADDR = 1
    const val ACK_BAD_CRC = 2
    const val ACK_BAD_PARAM = 3

    /** CRC-8/MAXIM-DOW: poly 0x31 reflected (= 0x8C, shift right), init 0x00, xorout 0x00. */
    fun crc8(data: ByteArray, from: Int = 0, to: Int = data.size): Int {
        var crc = 0
        for (i in from until to) {
            crc = crc xor (data[i].toInt() and 0xFF)
            repeat(8) {
                crc = if (crc and 1 != 0) (crc ushr 1) xor 0x8C else crc ushr 1
            }
        }
        return crc
    }

    /** Builds a complete packet: sync, LEN, SQN, ADDR, payload, CRC. */
    fun buildPacket(sqn: Int, addr: Int, payload: ByteArray): ByteArray {
        val len = 2 + payload.size + 1 // SQN + ADDR + payload + CRC
        val p = ByteArray(len + 3)
        p[0] = SYNC1.toByte()
        p[1] = SYNC2.toByte()
        p[2] = len.toByte()
        p[3] = sqn.toByte()
        p[4] = addr.toByte()
        payload.copyInto(p, 5)
        p[p.size - 1] = crc8(p, 2, p.size - 1).toByte()
        return p
    }

    /** Builds a reply packet from the car: ACK code followed by optional data. */
    fun reply(sqn: Int, ack: Int, data: ByteArray = ByteArray(0)): ByteArray =
        buildPacket(sqn, ADDR, byteArrayOf(ack.toByte()) + data)

    fun hex(bytes: ByteArray): String =
        bytes.joinToString(" ") { "%02X".format(it.toInt() and 0xFF) }

    /** Parses "AC 53 04", "ac5304" or "0xAC, 0x53". Returns null if the text isn't whole hex bytes. */
    fun parseHex(text: String): ByteArray? {
        val clean = text.replace("0x", "", ignoreCase = true).filter { !it.isWhitespace() && it != ',' }
        if (clean.isEmpty() || clean.length % 2 != 0) return null
        if (!clean.all { it in "0123456789abcdefABCDEF" }) return null
        return ByteArray(clean.length / 2) { clean.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
    }

    /** Human-readable form of a received command, for the log. */
    fun describeCommand(f: Frame): String {
        val tail = "  (sqn ${f.sqn}" + (if (f.addr != ADDR) ", addr ${f.addr})" else ")")
        if (!f.crcOk) return "BAD CRC$tail"
        val p = f.payload
        val code = p[0].toInt() and 0xFF
        val params = p.size - 1
        val text = when {
            code == SET_SPEED && params == 1 -> "SET_SPEED speed=${p[1]}"
            code == SET_ANGLE && params == 1 -> "SET_ANGLE angle=${p[1]}"
            code == SS_SA && params == 2 -> "SS_SA speed=${p[1]} angle=${p[2]}"
            code == GET_TEL && params == 0 -> "GET_TEL"
            code == SS_SA_GT && params == 2 -> "SS_SA_GT speed=${p[1]} angle=${p[2]}"
            code == LED && params == 1 -> "LED " + when (p[1].toInt()) { 0 -> "on"; 1 -> "off"; else -> "value=${p[1].toInt() and 0xFF}" }
            code == VERSION && params == 1 -> "VERSION module=${p[1].toInt() and 0xFF}"
            else -> "CODE 0x%02X with %d param byte(s)".format(code, params)
        }
        return text + tail
    }
}

/** One complete packet as it came off the wire (sync..CRC). */
class Frame(val bytes: ByteArray) {
    val len get() = u8(2)
    val sqn get() = u8(3)
    val addr get() = u8(4)
    val payload: ByteArray get() = bytes.copyOfRange(5, bytes.size - 1)
    val crcOk get() = Protocol.crc8(bytes, 2, bytes.size - 1) == u8(bytes.size - 1)

    private fun u8(i: Int) = bytes[i].toInt() and 0xFF
}

/**
 * Cuts a byte stream into packets, like the firmware's UART parser:
 * - skips junk until the sync word AC 53;
 * - if LEN is outside 4..253, drops the sync byte and looks for the next sync;
 * - drops a partial packet if the next bytes arrive more than [timeoutMs] later.
 * Not thread-safe: call it from one thread (the Bluetooth reader).
 */
class PacketParser(private val timeoutMs: Long = 100) {
    sealed class Event {
        class Packet(val frame: Frame) : Event()
        class Junk(val bytes: ByteArray, val reason: String) : Event()
    }

    private var buf = ByteArray(0)
    private var lastRxMs = 0L

    fun reset() {
        buf = ByteArray(0)
    }

    fun feed(data: ByteArray, nowMs: Long): List<Event> {
        val events = mutableListOf<Event>()
        if (buf.isNotEmpty() && nowMs - lastRxMs > timeoutMs) {
            events += Event.Junk(buf, "timeout: partial packet dropped")
            buf = ByteArray(0)
        }
        lastRxMs = nowMs
        buf += data

        val junk = ByteArrayOutputStream()
        fun drop(n: Int) {
            junk.write(buf, 0, n)
            buf = buf.copyOfRange(n, buf.size)
        }
        fun flushJunk() {
            if (junk.size() > 0) events += Event.Junk(junk.toByteArray(), "skipped (no sync / bad LEN)")
            junk.reset()
        }

        while (true) {
            val start = findSync()
            if (start < 0) {
                // No sync word yet. Keep a trailing AC: it may be the start of the next one.
                val keep = if (buf.isNotEmpty() && (buf.last().toInt() and 0xFF) == Protocol.SYNC1) 1 else 0
                drop(buf.size - keep)
                break
            }
            if (start > 0) drop(start)
            if (buf.size < 3) break // wait for LEN
            val len = buf[2].toInt() and 0xFF
            if (len < Protocol.MIN_LEN || len > Protocol.MAX_LEN) {
                drop(1) // not a real packet: skip this sync byte and resync
                continue
            }
            if (buf.size < len + 3) break // wait for the rest
            flushJunk()
            events += Event.Packet(Frame(buf.copyOfRange(0, len + 3)))
            buf = buf.copyOfRange(len + 3, buf.size)
        }
        flushJunk()
        return events
    }

    private fun findSync(): Int {
        for (i in 0 until buf.size - 1) {
            if ((buf[i].toInt() and 0xFF) == Protocol.SYNC1 && (buf[i + 1].toInt() and 0xFF) == Protocol.SYNC2) return i
        }
        return -1
    }
}
