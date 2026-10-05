# Interaction protocol v1.0

The protocol defines the exchange between the *products* (cars **M41** and **M43**,
and the CNC machine **Ч4П2У**) and their *control systems*: an Android phone over
Bluetooth or WiFi, a Nextion tablet over Bluetooth, and this PC app. All of them
share one packet format. This project uses the **M43** command set.

Every value below that the firmware might change is a setting in the profile
(`rc_controller/resources/profiles/default.toml`), not code.

## Link layer

- UART at **19200 baud, 8N1** (8 data bits, no parity, 1 stop bit), LSB first.
- A packet is sent without gaps between bytes. If a gap is unavoidable, it is
  at most 11 bit times (572.92 µs at 19200 baud).
- The exchange is request/response and **the control system always initiates it**.
  The product answers every valid packet. A packet shorter than the minimum length
  is ignored without a reply.
- Only one request is outstanding at a time: nothing new is sent until the reply
  comes back or the request times out.
- Multi-byte values are **little-endian**.

## Command packet (control system → product)

| Byte        | Field  | Notes                                                        |
|-------------|--------|--------------------------------------------------------------|
| 0           | `0xAC` | sync word, first byte                                        |
| 1           | `0x53` | sync word, second byte                                       |
| 2           | LEN    | number of bytes after LEN (SQN … CRC); range 4 … 253         |
| 3           | SQN    | sequence number                                              |
| 4           | ADDR   | device address, `1` for every device                         |
| 5 … LEN+1   | CMD    | CODE (1 byte), then PARAM (0 … n bytes); LEN−3 bytes in total |
| LEN+2       | CRC    | CRC-8                                                        |

A full packet is `LEN + 3` bytes long, at most 256 bytes.

- **SQN** is 0 when a session starts and increases by 1 with every packet sent,
  retransmissions included. It counts modulo 255: 0, 1, … 254, 0, … The product
  copies it into the reply.
- **CRC** is **CRC-8/MAXIM-DOW**: polynomial x⁸ + x⁵ + x⁴ + 1 (`0x31`), init `0x00`,
  reflected input and output, no final XOR (the same as Arduino's `OneWire::crc8`).
  check("123456789") = `0xA1`. It covers LEN, SQN, ADDR and CMD, i.e. every byte
  except the sync word and the CRC itself. The firmware reference is
  [`crc8.c`](crc8.c), which is self-testing.

## Reply packet (product → control system)

| Byte        | Field  | Notes                                                                      |
|-------------|--------|----------------------------------------------------------------------------|
| 0, 1        | `0xAC 0x53` | sync word                                                             |
| 2           | LEN    | number of bytes after LEN                                                  |
| 3           | SQN    | copied from the command this reply acknowledges                            |
| 4           | ADDR   | address of the replying device                                             |
| 5           | ACK    | `0` accepted, `1` wrong address, `2` CRC error, `3` invalid parameter      |
| 6 … LEN+1   | DATA   | telemetry or version payload; LEN−4 bytes, empty when nothing was requested |
| LEN+2       | CRC    | same algorithm as in the command packet                                    |

## Session behaviour

- The first telemetry request goes out within **3 s** of connecting. After that,
  telemetry is requested **at least once per second** (`keepalive_command`,
  `keepalive_period_ms`).
- The product replies within **50 ms**. The PC waits `reply_timeout_ms = 200` by
  default to leave room for Bluetooth latency; the echo test measures the real
  round-trip time over USB and Bluetooth.
- A command that gets no reply is retried **3 more times at 1 s intervals**
  (`retries`, `retry_interval_ms`), each time with a new SQN. If none of the
  retries gets a reply, the link is considered lost. Drive commands are never
  retried; a newer drive command replaces them.
- When commands stop arriving, the product goes into **safe mode and stops the
  motor**.
- The PC keeps a CSV log of every exchange, so the whole session can be handed
  to the developer after an abnormal situation.
- The product refreshes its telemetry fields on its own 1 s timer. An incoming
  request does not trigger a refresh.

## M43 commands

| Command   | CODE   | PARAM                     | LEN | Description                                     |
|-----------|--------|---------------------------|-----|-------------------------------------------------|
| SET_SPEED | `0x01` | INT8                      | 5   | set speed                                       |
| SET_ANGLE | `0x02` | INT8                      | 5   | set steering angle                              |
| SS_SA     | `0x03` | INT8 speed, INT8 angle    | 6   | set speed and angle in one packet               |
| GET_TEL   | `0x04` | none                      | 4   | request telemetry                               |
| SS_SA_GT  | `0x07` | INT8 speed, INT8 angle    | 6   | set speed and angle, and request telemetry      |
| LED       | `0xFE` | `0x00` / `0x01`           | 5   | LEDs: `0x00` = on, `0x01` = off                 |
| VERSION   | `0xFF` | module selector           | 5   | version request                                 |

INT8 values run from −127 to 127. The magnitude is the speed or angle and the
sign is the direction, with ±127 as the maximum and 0 meaning stop / straight
ahead. Positive speed is forward, positive angle is right.

There is no dedicated brake command. The macro step `brake` sends `drive.brake`
from the profile, which is currently the same as stop (`SS_SA 0 0`).

VERSION module selector:

| PARAM | Module | PARAM | Module |
|---|---|---|---|
| `0x00` | whole project | `0x05` | motor |
| `0x01` | accelerometer | `0x06` | steering drive |
| `0x02` | tachometers | `0x07` | WiFi |
| `0x03` | battery | `0x08` | Bluetooth |
| `0x04` | rangefinder | | |

The version reply is 2 bytes: the product ID (M41 = 1, Ч4П2У = 2, M43 = 3), then
the software version.

## Telemetry

This is the DATA part of a telemetry reply. Bytes are numbered from 1 inside DATA;
16-bit fields are little-endian (the lower-numbered byte is the low byte).

| Byte(s) | Type   | Validity bit | Name    | Meaning                           | Conversion                        |
|---------|--------|-----|-----------|-----------------------------------|-----------------------------------|
| 1       | uint8  | –   | TLM_NUM   | telemetry counter, +1 per request | –                                 |
| 3–2     | int16  | 7   | SPEED     | speed (accelerometer)             | v [m/s] = Y / 2¹⁵ · MAX_SPEED     |
| 5–4     | int16  | 6   | PITCH     | roll angle («крен»)               | deg = Y / 2¹⁵ · 90                |
| 7–6     | int16  | 5   | ROLL      | pitch angle («тангаж»)            | deg = Y / 2¹⁵ · 90                |
| 9–8     | int16  | 4   | RPM_MAG   | RPM from the magnetic tachometer  | rpm = Y / 2¹⁵ · MAX_RPM           |
| 11–10   | int16  | 3   | RPM_OPT   | RPM from the optical tachometer   | rpm = Y / 2¹⁵ · MAX_RPM           |
| 13–12   | uint16 | 2   | DIST      | distance to an obstacle           | S [cm] = Y / 2¹⁶ · 2000 / 100     |
| 14      | uint8  | 1   | BAT_LVL   | battery level                     | % = Y / 2⁸ · 100                  |
| 15      | uint8  | –   | VALID     | validity bitmask; bit *n* marks the field whose validity bit is *n* | – |

Note that the field named PITCH carries roll and the one named ROLL carries pitch.

## Worked examples

SQN = 0, ADDR = 1:

- `GET_TEL`: `AC 53 04 00 01 04 AB`
- `SS_SA` with speed +50 and angle −20: `AC 53 06 00 01 03 32 EC 2F`
- reply with ACK = 0 and no data: `AC 53 04 00 01 00 CA`

## HC-05 setup

(Details in the datasheets in [`../datasheets`](../datasheets).)

- A new module ships at 9600 or 38400 baud in data mode. Check it with
  `AT+UART?` and set the protocol rate once with `AT+UART=19200,0,0`.
- **AT mode:** hold KEY/PIO11 (pin 34) high at power-up and the module enters AT
  mode at 38400 baud. Commands are upper case and end with `\r\n`.
- The default PIN is `1234` (`0000` on some modules). Rename the module with
  `AT+NAME=<name>`.
- The module must be a slave (`AT+ROLE=0`) for the PC to connect to it.
- After pairing, the PC sees the module as a virtual COM port:
  - Windows: "Standard Serial over Bluetooth link", usually two ports. The
    *outgoing* one is the one to open.
  - Linux: `/dev/rfcomm0`, created with `rfcomm bind`.
  - macOS: `/dev/cu.<name>`.
