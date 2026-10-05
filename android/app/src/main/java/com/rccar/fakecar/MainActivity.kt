package com.rccar.fakecar

import android.Manifest
import android.app.Activity
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.view.View
import android.view.WindowInsets
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ListView
import android.widget.RadioGroup
import android.widget.TextView
import android.widget.Toast
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity(), BtServer.Listener {

    enum class Mode { FAKE_CAR, ECHO, SNIFF }

    private val car = FakeCar()
    private val parser = PacketParser() // used only on the Bluetooth thread
    private val ui = Handler(Looper.getMainLooper())
    private val timeFormat = SimpleDateFormat("HH:mm:ss.SSS", Locale.US) // used only on the UI thread

    // Shared between the UI thread and the Bluetooth thread.
    @Volatile private var server: BtServer? = null
    @Volatile private var connected = false
    @Volatile private var mode = Mode.FAKE_CAR
    @Volatile private var delayMs = 0L
    @Volatile private var dropReplies = false
    @Volatile private var corruptCrc = false
    @Volatile private var packets = 0
    @Volatile private var lastSqn = -1

    private lateinit var statusView: TextView
    private lateinit var stateView: TextView
    private lateinit var hexInput: EditText
    private val logLines = ArrayList<String>()
    private lateinit var logAdapter: ArrayAdapter<String>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        statusView = findViewById(R.id.status)
        stateView = findViewById(R.id.state)
        hexInput = findViewById(R.id.hexInput)
        logAdapter = ArrayAdapter(this, R.layout.log_line, logLines)
        findViewById<ListView>(R.id.log).adapter = logAdapter

        findViewById<RadioGroup>(R.id.modeGroup).setOnCheckedChangeListener { _, id ->
            mode = when (id) {
                R.id.modeEcho -> Mode.ECHO
                R.id.modeSniff -> Mode.SNIFF
                else -> Mode.FAKE_CAR
            }
            log("--", null, "mode: $mode")
        }
        findViewById<RadioGroup>(R.id.delayGroup).setOnCheckedChangeListener { _, id ->
            delayMs = when (id) {
                R.id.delay100 -> 100L
                R.id.delay300 -> 300L
                else -> 0L
            }
        }
        findViewById<CheckBox>(R.id.dropReplies).setOnCheckedChangeListener { _, on -> dropReplies = on }
        findViewById<CheckBox>(R.id.corruptCrc).setOnCheckedChangeListener { _, on -> corruptCrc = on }
        findViewById<Button>(R.id.send).setOnClickListener { sendHex() }
        findViewById<Button>(R.id.clear).setOnClickListener {
            logLines.clear()
            logAdapter.notifyDataSetChanged()
        }
        findViewById<Button>(R.id.btSettings).setOnClickListener {
            startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS))
        }
        statusView.setOnClickListener { startServer() } // retry after an error

        // Android 15+ draws apps edge-to-edge: keep our views clear of the status/navigation bars.
        if (Build.VERSION.SDK_INT >= 35) {
            val root = findViewById<View>(R.id.root)
            val pad = root.paddingLeft
            root.setOnApplyWindowInsetsListener { v, insets ->
                val bars = insets.getInsets(
                    WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout() or WindowInsets.Type.ime()
                )
                v.setPadding(pad + bars.left, pad + bars.top, pad + bars.right, pad + bars.bottom)
                insets
            }
        }
    }

    // The server runs while the app is on screen (the layout keeps the screen on meanwhile).
    override fun onStart() {
        super.onStart()
        startServer()
        ui.post(tick)
    }

    override fun onStop() {
        super.onStop()
        ui.removeCallbacks(tick)
        server?.stop()
        server = null
        connected = false
        showStatus("Stopped (app in background)", COLOR_GREY)
    }

    private fun startServer() {
        if (server != null) return
        if (Build.VERSION.SDK_INT >= 31 &&
            checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.BLUETOOTH_CONNECT), REQ_PERMISSION)
            return
        }
        val adapter = getSystemService(BluetoothManager::class.java)?.adapter
        if (adapter == null) {
            showStatus("Error: this phone has no Bluetooth", COLOR_RED)
            return
        }
        if (!adapter.isEnabled) {
            showStatus("Bluetooth is off. Tap here to retry", COLOR_RED)
            startActivityForResult(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE), REQ_ENABLE_BT)
            return
        }
        server = BtServer(adapter, this).also { it.start() }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, results)
        if (requestCode != REQ_PERMISSION) return
        if (results.firstOrNull() == PackageManager.PERMISSION_GRANTED) startServer()
        else showStatus("Error: Bluetooth permission denied. Tap here to retry", COLOR_RED)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_ENABLE_BT && resultCode == RESULT_OK) startServer()
    }

    // ---- BtServer.Listener: these run on the Bluetooth thread ----

    private var lastError = "" // Bluetooth thread only

    override fun onListening() {
        lastError = ""
        ui.post { showStatus("Listening as \"${BtServer.SERVICE_NAME}\"…", COLOR_ORANGE) }
    }

    override fun onConnected(name: String) {
        parser.reset()
        car.onConnected(SystemClock.elapsedRealtime())
        connected = true
        log("--", null, "connected: $name")
        ui.post { showStatus("Connected to $name", COLOR_GREEN) }
    }

    override fun onDisconnected() {
        connected = false
        log("--", null, "disconnected")
    }

    override fun onError(message: String) {
        connected = false
        if (message != lastError) log("!!", null, "Bluetooth error: $message (retrying every 2 s)")
        lastError = message
        ui.post { showStatus("Error: $message", COLOR_RED) }
    }

    override fun onData(data: ByteArray) {
        val now = SystemClock.elapsedRealtime()
        if (mode == Mode.ECHO) {
            log("RX", data, "echo")
            if (server?.send(data) == true) log("TX", data, "echo")
            return
        }
        for (event in parser.feed(data, now)) {
            when (event) {
                is PacketParser.Event.Junk -> log("RX", event.bytes, event.reason)
                is PacketParser.Event.Packet -> {
                    val frame = event.frame
                    packets++
                    lastSqn = frame.sqn
                    log("RX", frame.bytes, Protocol.describeCommand(frame))
                    if (mode == Mode.FAKE_CAR) sendReply(car.handle(frame, now))
                }
            }
        }
    }

    /** Sends a car reply, applying the test toggles. */
    private fun sendReply(reply: FakeCar.Reply) {
        if (dropReplies) {
            log("TX", null, "reply dropped (test toggle): ${reply.text}")
            return
        }
        var bytes = reply.bytes
        var text = reply.text
        if (corruptCrc) {
            bytes = bytes.copyOf()
            bytes[bytes.size - 1] = (bytes[bytes.size - 1].toInt() xor 0xFF).toByte()
            text += "  [CRC corrupted]"
        }
        val delay = delayMs
        if (delay > 0) {
            SystemClock.sleep(delay)
            text += "  [delayed $delay ms]"
        }
        if (server?.send(bytes) == true) log("TX", bytes, text)
        else log("!!", bytes, "send failed: $text")
    }

    // ---- UI thread ----

    /** Every 100 ms: safe-mode check and refresh of the state line. */
    private val tick = object : Runnable {
        override fun run() {
            if (connected && mode == Mode.FAKE_CAR && car.checkSafeMode(SystemClock.elapsedRealtime())) {
                log("!!", null, "SAFE MODE: no commands, motor stopped")
            }
            val sqn = if (lastSqn < 0) "-" else lastSqn.toString()
            stateView.text = "Speed %4d   Angle %4d   LED %s\nPackets %d   Last SQN %s".format(
                car.speed, car.angle, if (car.ledOn) "ON" else "off", packets, sqn
            )
            ui.postDelayed(this, 100)
        }
    }

    private fun sendHex() {
        val bytes = Protocol.parseHex(hexInput.text.toString())
        if (bytes == null) {
            toast("Enter hex bytes, e.g. AC 53 04 00 01 04 AB")
            return
        }
        val s = server
        if (!connected || s == null) {
            toast("Not connected")
            return
        }
        Thread { if (s.send(bytes)) log("TX", bytes, "manual") else log("!!", bytes, "send failed") }.start()
    }

    private fun showStatus(text: String, color: Int) {
        statusView.text = text
        statusView.setTextColor(color)
    }

    /** Adds a line to the log. Safe to call from any thread. */
    private fun log(dir: String, bytes: ByteArray?, text: String) {
        val time = System.currentTimeMillis()
        val hex = if (bytes != null) "  " + Protocol.hex(bytes) else ""
        ui.post {
            logLines.add("$dir ${timeFormat.format(Date(time))}$hex  $text")
            if (logLines.size > MAX_LOG_LINES) logLines.subList(0, logLines.size - MAX_LOG_LINES).clear()
            logAdapter.notifyDataSetChanged()
        }
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_SHORT).show()

    companion object {
        private const val REQ_PERMISSION = 1
        private const val REQ_ENABLE_BT = 2
        private const val MAX_LOG_LINES = 500
        private val COLOR_GREEN = Color.rgb(0x2E, 0x7D, 0x32)
        private val COLOR_ORANGE = Color.rgb(0xE6, 0x51, 0x00)
        private val COLOR_RED = Color.rgb(0xC6, 0x28, 0x28)
        private val COLOR_GREY = Color.GRAY
    }
}
