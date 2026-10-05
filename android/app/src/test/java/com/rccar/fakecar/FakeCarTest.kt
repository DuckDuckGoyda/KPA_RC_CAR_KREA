package com.rccar.fakecar

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.random.Random

class FakeCarTest {
    private val car = FakeCar(Random(42))

    /** Sends a command to the car and parses its reply as the PC would. */
    private fun send(packet: ByteArray, nowMs: Long = 0): Frame {
        val cmd = (PacketParser().feed(packet, 0).single() as PacketParser.Event.Packet).frame
        val reply = car.handle(cmd, nowMs).bytes
        val frame = (PacketParser().feed(reply, 0).single() as PacketParser.Event.Packet).frame
        assertTrue("reply CRC must be valid", frame.crcOk)
        return frame
    }

    private fun cmd(sqn: Int, vararg payload: Int, addr: Int = 1) =
        Protocol.buildPacket(sqn, addr, ByteArray(payload.size) { payload[it].toByte() })

    private fun ack(f: Frame) = f.payload[0].toInt()

    @Test fun ssSaIsAcceptedWithExactReply() {
        val reply = car.handle(Frame(cmd(0, 0x03, 50, -20)), 0)
        assertEquals("AC 53 04 00 01 00 CA", Protocol.hex(reply.bytes))
        assertEquals(50, car.speed)
        assertEquals(-20, car.angle)
    }

    @Test fun replyCopiesSqn() {
        assertEquals(0x7B, send(cmd(0x7B, 0x01, 10)).sqn)
    }

    @Test fun badCrcGivesAck2WithReceivedSqn() {
        val f = send(hex("AC 53 04 09 01 04 00")) // GET_TEL with a wrong CRC byte
        assertEquals(Protocol.ACK_BAD_CRC, ack(f))
        assertEquals(9, f.sqn)
        assertEquals(4, f.len) // no data
    }

    @Test fun wrongAddressGivesAck1() {
        assertEquals(Protocol.ACK_BAD_ADDR, ack(send(cmd(1, 0x04, addr = 2))))
    }

    @Test fun invalidCommandsGiveAck3() {
        assertEquals(3, ack(send(cmd(1, 0x10))))            // unknown code
        assertEquals(3, ack(send(cmd(1, 0x01))))            // SET_SPEED without param
        assertEquals(3, ack(send(cmd(1, 0x01, 5, 5))))      // SET_SPEED with 2 params
        assertEquals(3, ack(send(cmd(1, 0x01, -128))))      // speed out of range
        assertEquals(3, ack(send(cmd(1, 0x03, 10, -128))))  // angle out of range
        assertEquals(3, ack(send(cmd(1, 0x04, 0))))         // GET_TEL with a param
        assertEquals(3, ack(send(cmd(1, 0xFE, 2))))         // LED value
        assertEquals(3, ack(send(cmd(1, 0xFF, 9))))         // VERSION module
        assertEquals(0, car.speed)                          // nothing was applied
    }

    @Test fun setSpeedAndAngleSeparately() {
        assertEquals(0, ack(send(cmd(1, 0x01, -127))))
        assertEquals(0, ack(send(cmd(2, 0x02, 127))))
        assertEquals(-127, car.speed)
        assertEquals(127, car.angle)
    }

    @Test fun ledIsInverted() {
        send(cmd(1, 0xFE, 0))
        assertTrue(car.ledOn)
        send(cmd(2, 0xFE, 1))
        assertFalse(car.ledOn)
    }

    @Test fun versionReply() {
        val f = send(cmd(1, 0xFF, 0))
        assertEquals("00 03 01", Protocol.hex(f.payload)) // ACK 0, PRODUCT 3, SW 1
    }

    @Test fun telemetryReply() {
        send(cmd(1, 0x01, 127))
        val f = send(cmd(2, 0x04))
        assertEquals(19, f.len)
        assertEquals(22, f.bytes.size)
        assertEquals(2, f.sqn)
        assertEquals(0, ack(f))
        val data = ByteBuffer.wrap(f.payload, 1, 15).order(ByteOrder.LITTLE_ENDIAN)
        assertEquals(1, data.get().toInt())            // TLM_NUM
        assertEquals(32767, data.short.toInt())        // SPEED = 127 / 127 * 32767
        data.short; data.short                         // PITCH, ROLL
        assertEquals(127 * 40, data.short.toInt())     // RPM_MAG
        data.short; data.short                         // RPM_OPT, DIST
        assertTrue((data.get().toInt() and 0xFF) in 200..230) // BAT_LVL
        assertEquals(0xFE, data.get().toInt() and 0xFF)       // VALID
        // TLM_NUM increments
        assertEquals(2, send(cmd(3, 0x04)).payload[1].toInt())
    }

    @Test fun ssSaGtSetsAndReturnsTelemetry() {
        val f = send(cmd(4, 0x07, -50, 20))
        assertEquals(19, f.len)
        assertEquals(-50, car.speed)
        assertEquals(20, car.angle)
        val speed = ByteBuffer.wrap(f.payload, 2, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt()
        assertEquals(-50 * 32767 / 127, speed)
    }

    @Test fun safeModeStopsAfterOneSecond() {
        car.onConnected(0)
        send(cmd(1, 0x03, 60, 0), nowMs = 1000)
        assertFalse(car.checkSafeMode(1900))
        assertEquals(60, car.speed)
        assertTrue(car.checkSafeMode(2001))
        assertEquals(0, car.speed)
        assertFalse(car.checkSafeMode(3000)) // only reported once
    }

    @Test fun invalidPacketsDoNotFeedSafeMode() {
        car.onConnected(0)
        send(cmd(1, 0x01, 60), nowMs = 0)
        send(hex("AC 53 04 09 01 04 00"), nowMs = 900) // bad CRC
        assertTrue(car.checkSafeMode(1001))
    }
}
