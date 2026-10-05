# Architecture

This is a developer-facing overview. The user-facing text (UI, help, README) is in Russian; code, comments and this file are in English.

## Layers

```
gui/        PyQt6 widgets. Only presentation and wiring; no protocol knowledge.
  │
runtime/    Qt objects with timers and signals, but no widgets:
  │           SerialTransport  pyserial + reader thread → data_received
  │           LinkManager      request/response, SQN, timeouts, retries, keepalive, log entries
  │           DriveController  keyboard / gamepad / macro → drive commands on the link
  │           MacroRunner      executes flattened macro steps
  │           GamepadPoller    XInput via ctypes (Windows)
  │           EchoTester       raw echo test, pauses the link while running
  │           SessionLogger    CSV of every log entry
  │
control/    pure Python: DriveModel (ramping), mix() (speed/angle or differential),
  │         macro language parser.
  │
protocol/   pure Python: Crc8, PacketFormat + FrameParser, Profile (TOML loader with
            validation), command-line encoding and reply decoding.
```

`protocol/` and `control/` never import Qt, so most of the logic is unit-tested without a display. The runtime layer is tested with pytest-qt, using a fake transport and pyserial's `loop://`. The GUI has offscreen smoke tests.

## Data flow

**Sending a command:**

1. Text such as `SS_SA 50 -20` comes from the terminal, a drive template or a macro.
2. `encode_command(profile, text)` turns it into payload bytes (CODE + PARAM).
3. `LinkManager.send_command()` queues the request. Drive requests carry a coalesce key, so a newer one replaces a queued older one.
4. `_pump()` sends the next ready request. `PacketFormat.encode(sqn, payload)` builds the packet, `SerialTransport.write()` sends it, and a reply timer starts.

**Receiving:**

1. The reader thread emits `data_received(bytes)`.
2. `FrameParser.feed()` produces `Frame`, `BadFrame` and `Junk` events.
3. A `Frame` whose SQN matches the request in flight is its reply. It is decoded with `decode_reply()` using that command's reply layout.
4. The link state becomes OK, and a log entry is emitted.

There are three special cases:

- A frame identical to the sent one is an **echo**, which counts as alive.
- A frame whose SQN matches a timed-out request is **late**.
- Anything else is **unsolicited**.

## Key decisions

- **Everything car-specific lives in the profile** (`resources/profiles/default.toml`). Command names, codes, parameters, reply layouts, the drive templates, CRC variant, keys and gamepad mapping are all data, so firmware changes need no code changes. `parse_profile()` validates strictly: unknown keys, wrong types, and drive templates that would overflow a parameter all fail at load time with a Russian message that names the key.
- **The CRC is CRC-8/MAXIM-DOW.** `docs/protocol/crc8.c` is the firmware reference and shares test vectors with `tests/test_framing.py`.
- **One command syntax everywhere**: the terminal, drive templates (`SS_SA {speed} {angle}`) and macro `send` steps all go through `encode_command()`.
- **Drive commands are never retried** and are coalesced, so the car never receives a stale speed. Terminal and macro commands are retried 3 times at 1 s intervals.
- **Safety**:
  - The window losing focus, or focus moving into a text field, releases all keys.
  - Esc is an emergency stop regardless of focus.
  - After an emergency stop, the gamepad is ignored until it returns to neutral.
  - Macros abort on any manual input or link loss, and always end with a stop.
  - Disconnecting while moving sends the stop command before the port closes.
- **Keys are layout-independent**: bindings match the Qt key, the same physical key on ЙЦУКЕН, and the Windows virtual-key code.
- **XInput via ctypes** instead of pygame or SDL. It adds no dependency and keeps the exe small. The trade-off is that PlayStation pads need Steam or DS4Windows.
- **Port opening runs on a worker thread**, because opening an HC-05 outgoing COM port makes Windows page the module, which can block for seconds.

## Extending

- **New command:** add a `[[command]]` block to the profile. No code change.
- **Different drive command(s):** edit `[drive] command`. It may be a list, for example `["SET_SPEED {speed}", "SET_ANGLE {angle}"]`.
- **New macro action:**
  1. Add the keyword and its aliases to `_KEYWORDS` in `control/macro.py`.
  2. Parse it into a step dataclass.
  3. Execute it in `MacroRunner._advance()`.
  4. Add it to the cheat sheet (`gui/macro_panel.py`) and to `resources/help/05_macros.md`.
- **Another transport** (for example a direct RFCOMM socket): implement the `SerialTransport` signal and method surface (`opening`, `opened`, `open_failed`, `closed`, `data_received`, `data_sent`, `open`, `close`, `write`, `is_open`, `port`). `LinkManager` only relies on that surface; the tests' `FakeTransport` is a minimal example.
- **Telemetry display:** the decoded fields are already produced by `decode_reply()`. A panel could subscribe to `LinkManager.log_entry`, or better, to a dedicated signal added next to it.

## Build and CI

`packaging/rc_controller.spec` builds a single windowed `RC_Controller.exe`. Resources are bundled; `paths.ensure_user_dirs()` copies the profiles and macros next to the exe on first start, so they stay editable.

`.github/workflows/build.yml` runs on every push, on Windows:

1. `ruff check`
2. pytest (offscreen)
3. the PyInstaller build
4. `RC_Controller.exe --smoke-test`, which opens `loop://`, sends a command and exits 0 when the echo is recognised
5. uploads the exe as an artifact
