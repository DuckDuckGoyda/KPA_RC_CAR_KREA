# Fake RC Car (Android)

An Android app that pretends to be the RC car's HC-05 + firmware. It opens a Bluetooth SPP
(serial port) server called **"RC Fake Car"**. The PC connects to it through a Bluetooth COM port,
exactly as it would to the HC-05, and the phone answers every packet the way the firmware should.

Requires Android 8.0+.

## 1. Build

Open the `android` folder in Android Studio and run the app, or build from the command line
(JDK 17 in `JAVA_HOME`, Android SDK path in `local.properties` or `ANDROID_HOME`):

```
gradlew test assembleDebug
```

This runs the unit tests (`app/src/test`) and writes the debug-signed APK to
`app/build/outputs/apk/debug/app-debug.apk`. On Linux and macOS use `./gradlew`.

## 2. Install on the phone

**Option A: copy the file.** Send `app-debug.apk` to the phone (USB cable → phone storage, Telegram
"Saved Messages", Google Drive…). Tap it on the phone. Android will ask you to allow installing
from that app ("Install unknown apps"). Allow it and tap Install. Play Protect may warn about an
unknown developer: choose "Install anyway".

**Option B: adb.** On the phone enable Developer options (tap Build number 7 times) → USB debugging.
Connect by USB, accept the prompt on the phone, then on the PC:

```
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

On first start (Android 12+) the app asks for the **Nearby devices** permission. Allow it.
The status line should turn orange: *Listening as "RC Fake Car"…*

## 3. Pair with Windows and get the COM port

1. Start the app on the phone and leave it open. It must be on screen whenever the PC connects.
   The app keeps the screen on, but the server stops if you switch to another app.
2. On the phone, tap **Bluetooth settings** in the app. While that screen is open the phone is
   visible to other devices.
3. On Windows: *Settings → Devices → Bluetooth & other devices → Add Bluetooth or other device →
   Bluetooth*, then pick the phone. Confirm the same code on both screens.
4. Go back to the Fake RC Car app on the phone. It must show *Listening*, because Windows reads
   the service list from the phone in the next step.
5. On Windows: *Bluetooth & other devices → More Bluetooth options* (under "Related settings" on
   the right) → **COM Ports** tab.
   - If a port for the phone marked **Outgoing** with service **'RC Fake Car'** is already listed,
     note its number.
   - Otherwise: **Add… → Outgoing**, then choose the phone under "Device that will use the COM port".
     Pick the service **RC Fake Car** and click OK. Note the new port, for example `COM5`.

## 4. Connect from the PC app

In the PC app choose the **Outgoing** COM port from step 3 (e.g. `COM5`) and open it. The baud rate
doesn't matter on a Bluetooth virtual COM port. Opening takes 1–5 s while Windows makes the
Bluetooth connection. The phone then shows **Connected to <PC name>** in green, and every
packet appears in the log.

Do not use an *Incoming* port. That one is for devices that connect to the PC, and it will never
reach the phone.

## Using the app

| Control | What it does |
|---|---|
| **Fake car** (default) | Full firmware behaviour: ACK codes, telemetry, safe mode |
| **Echo** | Sends back every received byte unchanged (like the "echo firmware") |
| **Sniff** | Never replies, only logs the decoded packets |
| Reply delay 0/100/300 ms | Delays each reply. Try 300 ms to trigger the PC's 200 ms timeout and retries |
| Drop replies | Receives and applies commands but sends no reply |
| Corrupt reply CRC | Flips the CRC byte of each reply, so the PC should report a CRC error |
| Hex field + Send | Sends any bytes, e.g. `AC 53 04 00 01 00 CA` (spaces optional) |

The test toggles apply in Fake car mode. **Safe mode**: when the car is moving and no valid packet
arrives for 1 s, speed goes to 0 and the log shows `SAFE MODE: no commands, motor stopped`.

Log lines: `RX`/`TX` = bytes in/out, `--` = connection events, `!!` = errors, safe mode, failed sends.
Tap the status line to retry after an error, e.g. once Bluetooth is back on.

## Troubleshooting

- **"RC Fake Car" is not in the service list** when adding the COM port: open the app on the phone
  (status *Listening*) and try again. If it still isn't there, remove the phone in Windows
  Bluetooth settings and pair again with the app open.
- **The PC app can't open the port / times out**: the app must be on screen on the phone. Check that you
  picked the *Outgoing* port. Toggling Bluetooth on the phone off and on also helps.
- **Connected, but no replies**: check that the mode is *Fake car* and "Drop replies" is off.

## Code map

| File | What's in it |
|---|---|
| `Protocol.kt` | Packet format, CRC-8/MAXIM-DOW, `PacketParser` (resync, 100 ms timeout), log decoding |
| `FakeCar.kt` | Car state, command handling → reply bytes, telemetry, safe mode |
| `BtServer.kt` | SPP server socket, accept/read thread, `send()` |
| `MainActivity.kt` | UI, permissions, modes and test toggles, log |

`Protocol.kt` and `FakeCar.kt` have no Android dependencies and are covered by the JVM tests
(`ProtocolTest`, `FakeCarTest`), including the protocol's worked examples.
