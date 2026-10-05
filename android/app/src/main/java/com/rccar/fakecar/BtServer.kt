package com.rccar.fakecar

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothServerSocket
import android.bluetooth.BluetoothSocket
import android.os.SystemClock
import java.io.Closeable
import java.io.IOException
import java.util.UUID

/**
 * Bluetooth SPP server, the phone's stand-in for the HC-05.
 *
 * One background thread: open the server socket (this publishes the SPP record that Windows
 * needs), accept one client, read from it until it disconnects, then accept the next one.
 * The server socket stays open until [stop]. A BtServer is single-use: start() once, stop() once.
 */
@SuppressLint("MissingPermission") // MainActivity checks BLUETOOTH_CONNECT before creating us
class BtServer(private val adapter: BluetoothAdapter, private val listener: Listener) {

    /** All callbacks run on the Bluetooth thread, not the UI thread. */
    interface Listener {
        fun onListening()
        fun onConnected(name: String)
        fun onDisconnected()
        fun onError(message: String)
        fun onData(data: ByteArray)
    }

    @Volatile private var running = false
    @Volatile private var serverSocket: BluetoothServerSocket? = null
    @Volatile private var client: BluetoothSocket? = null
    private val writeLock = Any()

    fun start() {
        running = true
        Thread(::acceptLoop, "bt-server").start()
    }

    fun stop() {
        running = false
        closeQuietly(client)       // unblocks read()
        closeQuietly(serverSocket) // unblocks accept()
    }

    /** Writes to the connected client. Returns false if nobody is connected or the write failed. */
    fun send(data: ByteArray): Boolean {
        val socket = client ?: return false
        return try {
            synchronized(writeLock) {
                socket.outputStream.write(data)
                socket.outputStream.flush()
            }
            true
        } catch (e: IOException) {
            false
        }
    }

    private fun acceptLoop() {
        while (running) {
            var ss: BluetoothServerSocket? = null
            try {
                ss = adapter.listenUsingRfcommWithServiceRecord(SERVICE_NAME, SPP_UUID)
                serverSocket = ss
                if (!running) break // stop() ran before we stored the socket; finally closes it
                while (running) {
                    notify { onListening() }
                    val socket = ss.accept() // blocks until the PC opens its COM port
                    client = socket
                    if (!running) { // same race for the client socket
                        closeQuietly(socket)
                        break
                    }
                    val device = socket.remoteDevice
                    notify { onConnected(device.name ?: device.address) }
                    readLoop(socket)
                    client = null
                    closeQuietly(socket)
                    notify { onDisconnected() }
                }
            } catch (e: IOException) {
                // accept() failed: Bluetooth switched off, or stop() closed the socket.
                notify { onError(e.message ?: "Bluetooth error") }
                if (running) SystemClock.sleep(RETRY_MS)
            } finally {
                closeQuietly(ss)
            }
        }
    }

    private fun readLoop(socket: BluetoothSocket) {
        val buf = ByteArray(1024)
        try {
            val input = socket.inputStream
            while (true) {
                val n = input.read(buf) // blocks; -1 or IOException when the PC disconnects
                if (n < 0) break
                if (n > 0) notify { onData(buf.copyOf(n)) }
            }
        } catch (e: IOException) {
            // connection lost
        }
    }

    /** Don't report anything after stop(): a new server may already be running. */
    private inline fun notify(block: Listener.() -> Unit) {
        if (running) listener.block()
    }

    private fun closeQuietly(c: Closeable?) {
        try {
            c?.close()
        } catch (e: IOException) {
        }
    }

    companion object {
        const val SERVICE_NAME = "RC Fake Car"
        val SPP_UUID: UUID = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")
        private const val RETRY_MS = 2000L
    }
}
