package com.rccar.fakecar

import com.rccar.fakecar.Protocol.ACK_BAD_ADDR
import com.rccar.fakecar.Protocol.ACK_BAD_CRC
import com.rccar.fakecar.Protocol.ACK_BAD_PARAM
import com.rccar.fakecar.Protocol.ACK_OK
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.random.Random

/** Telemetry DATA block: 15 bytes, little-endian. */
data class Telemetry(
    val num: Int,     // uint8, +1 per request
    val speed: Int,   // int16, speed / 127 * 32767
    val pitch: Int,   // int16
    val roll: Int,    // int16
    val rpmMag: Int,  // int16
    val rpmOpt: Int,  // int16
    val dist: Int,    // uint16
    val battery: Int, // uint8
    val valid: Int = 0xFE,
) {
    fun toBytes(): ByteArray = ByteBuffer.allocate(15).order(ByteOrder.LITTLE_ENDIAN)
        .put(num.toByte())
        .putShort(speed.toShort())
        .putShort(pitch.toShort())
        .putShort(roll.toShort())
        .putShort(rpmMag.toShort())
        .putShort(rpmOpt.toShort())
        .putShort(dist.toShort())
        .put(battery.toByte())
        .put(valid.toByte())
        .array()

    override fun toString() =
        "TEL #$num speed=$speed pitch=$pitch roll=$roll rpm=$rpmMag/$rpmOpt dist=$dist bat=$battery"
}

/**
 * The simulated car: applies commands and builds the reply the firmware should send.
 * Called from the Bluetooth thread (handle) and the UI thread (checkSafeMode), hence @Synchronized.
 */
class FakeCar(private val random: Random = Random.Default) {
    /** Reply bytes plus a short description for the log. */
    class Reply(val bytes: ByteArray, val text: String)

    @Volatile var speed = 0; private set
    @Volatile var angle = 0; private set
    @Volatile var ledOn = false; private set

    private var tlmNum = 0
    private var dist = 1500
    private var battery = 230.0
    private var lastValidMs = 0L

    @Synchronized
    fun handle(f: Frame, nowMs: Long): Reply {
        if (!f.crcOk) return ack(f.sqn, ACK_BAD_CRC) // SQN as received, even if it may be damaged
        if (f.addr != Protocol.ADDR) return ack(f.sqn, ACK_BAD_ADDR)

        val p = f.payload
        val code = p[0].toInt() and 0xFF
        val params = p.size - 1
        fun s8(i: Int) = p[i].toInt()             // int8
        fun u8(i: Int) = p[i].toInt() and 0xFF    // uint8
        fun ok(v: Int) = v in -127..127

        fun accepted(data: ByteArray = NO_DATA, text: String = "ACK 0") =
            Reply(Protocol.reply(f.sqn, ACK_OK, data), text)
        fun withTelemetry() = telemetry().let { accepted(it.toBytes(), "ACK 0  $it") }

        val reply: Reply? = when (code) {
            Protocol.SET_SPEED ->
                if (params == 1 && ok(s8(1))) { speed = s8(1); accepted() } else null
            Protocol.SET_ANGLE ->
                if (params == 1 && ok(s8(1))) { angle = s8(1); accepted() } else null
            Protocol.SS_SA ->
                if (params == 2 && ok(s8(1)) && ok(s8(2))) { speed = s8(1); angle = s8(2); accepted() } else null
            Protocol.GET_TEL ->
                if (params == 0) withTelemetry() else null
            Protocol.SS_SA_GT ->
                if (params == 2 && ok(s8(1)) && ok(s8(2))) { speed = s8(1); angle = s8(2); withTelemetry() } else null
            Protocol.LED -> // inverted, as in the spec: 0 = ON, 1 = OFF
                if (params == 1 && u8(1) <= 1) { ledOn = u8(1) == 0; accepted() } else null
            Protocol.VERSION ->
                if (params == 1 && u8(1) <= 8) {
                    accepted(byteArrayOf(PRODUCT, SW_VERSION), "ACK 0  VERSION product=$PRODUCT sw=$SW_VERSION")
                } else null
            else -> null
        }
        if (reply == null) return ack(f.sqn, ACK_BAD_PARAM)

        lastValidMs = nowMs
        return reply
    }

    /** Resets the safe-mode timer when a PC connects. */
    @Synchronized
    fun onConnected(nowMs: Long) {
        lastValidMs = nowMs
    }

    /** Safe mode: moving but no valid packet for 1 s -> stop. Returns true when it just triggered. */
    @Synchronized
    fun checkSafeMode(nowMs: Long): Boolean {
        if (speed != 0 && nowMs - lastValidMs > SAFE_MODE_MS) {
            speed = 0
            return true
        }
        return false
    }

    private fun telemetry(): Telemetry {
        tlmNum = (tlmNum + 1) and 0xFF
        dist = (dist + random.nextInt(-40, 41)).coerceIn(200, 4000)
        battery = (battery - 0.05).coerceAtLeast(100.0)
        val rpm = speed * 40
        return Telemetry(
            num = tlmNum,
            speed = speed * 32767 / 127,
            pitch = random.nextInt(-300, 301),
            roll = random.nextInt(-300, 301),
            rpmMag = rpm,
            rpmOpt = if (rpm == 0) 0 else rpm + random.nextInt(-30, 31),
            dist = dist,
            battery = battery.toInt(),
        )
    }

    /** Error reply (no data). */
    private fun ack(sqn: Int, ack: Int) = Reply(Protocol.reply(sqn, ack), "ACK $ack (${ACK_NAMES[ack]})")

    companion object {
        const val SAFE_MODE_MS = 1000L
        const val PRODUCT: Byte = 3
        const val SW_VERSION: Byte = 1
        private val NO_DATA = ByteArray(0)
        private val ACK_NAMES = listOf("accepted", "wrong address", "CRC error", "invalid parameter")
    }
}
