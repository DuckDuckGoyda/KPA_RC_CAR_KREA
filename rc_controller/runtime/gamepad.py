"""Gamepad input through XInput (Windows, Xbox-compatible controllers).

XInput ships with Windows, so this needs no extra dependency and keeps the
exe small. Most PC gamepads speak XInput; PlayStation controllers need Steam
or DS4Windows to show up as one.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..protocol.profile import GamepadSettings

BUTTON_MASKS = {
    "DPAD_UP": 0x0001,
    "DPAD_DOWN": 0x0002,
    "DPAD_LEFT": 0x0004,
    "DPAD_RIGHT": 0x0008,
    "START": 0x0010,
    "BACK": 0x0020,
    "LS": 0x0040,
    "RS": 0x0080,
    "LB": 0x0100,
    "RB": 0x0200,
    "A": 0x1000,
    "B": 0x2000,
    "X": 0x4000,
    "Y": 0x8000,
}

POLL_MS = 16
RESCAN_TICKS = 60  # look for a newly plugged-in pad about once a second


@dataclass(frozen=True)
class GamepadState:
    index: int = 0
    buttons: int = 0
    lt: float = 0.0  # 0..1
    rt: float = 0.0
    lx: float = 0.0  # -1..1, + = right
    ly: float = 0.0  # -1..1, + = up
    rx: float = 0.0
    ry: float = 0.0

    def pressed(self, name: str) -> bool:
        return bool(self.buttons & BUTTON_MASKS.get(name, 0))


def apply_deadzone(value: float, deadzone: float) -> float:
    magnitude = abs(value)
    if magnitude <= deadzone:
        return 0.0
    scaled = (magnitude - deadzone) / (1.0 - deadzone)
    return min(1.0, scaled) * (1 if value > 0 else -1)


def map_gamepad(state: GamepadState, settings: GamepadSettings) -> tuple[float, float]:
    """Gamepad state -> (throttle, steer), both -1..1."""
    dz = settings.deadzone
    if settings.throttle == "auto":
        # Triggers and the left stick's vertical both work, whichever the driver reaches for.
        throttle = apply_deadzone(state.rt, dz) - apply_deadzone(state.lt, dz) + apply_deadzone(state.ly, dz)
    elif settings.throttle == "triggers":
        throttle = apply_deadzone(state.rt, dz) - apply_deadzone(state.lt, dz)
    elif settings.throttle == "left_stick":
        throttle = apply_deadzone(state.ly, dz)
    else:
        throttle = apply_deadzone(state.ry, dz)
    axis = state.lx if settings.steering == "left_stick" else state.rx
    return max(-1.0, min(1.0, throttle)), apply_deadzone(axis, dz)


class _XInputGamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XInputState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint32), ("Gamepad", _XInputGamepad)]


class XInputBackend:
    ERROR_SUCCESS = 0

    def __init__(self) -> None:
        self._get_state = None
        self.dll_name = ""
        if sys.platform != "win32":
            return
        for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                dll = ctypes.WinDLL(name)  # type: ignore[attr-defined]
            except OSError:
                continue
            func = dll.XInputGetState
            func.argtypes = [ctypes.c_uint32, ctypes.POINTER(_XInputState)]
            func.restype = ctypes.c_uint32
            self._get_state = func
            self.dll_name = name
            break

    @property
    def available(self) -> bool:
        return self._get_state is not None

    def read(self, index: int) -> GamepadState | None:
        if self._get_state is None:
            return None
        state = _XInputState()
        if self._get_state(index, ctypes.byref(state)) != self.ERROR_SUCCESS:
            return None
        pad = state.Gamepad

        def stick(value: int) -> float:
            return max(-1.0, min(1.0, value / 32767))

        return GamepadState(
            index=index,
            buttons=pad.wButtons,
            lt=pad.bLeftTrigger / 255,
            rt=pad.bRightTrigger / 255,
            lx=stick(pad.sThumbLX),
            ly=stick(pad.sThumbLY),
            rx=stick(pad.sThumbRX),
            ry=stick(pad.sThumbRY),
        )


class GamepadPoller(QObject):
    state_changed = pyqtSignal(object)  # GamepadState
    connection_changed = pyqtSignal(bool, str)  # connected, description
    button_pressed = pyqtSignal(str)

    def __init__(self, backend: XInputBackend | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend if backend is not None else XInputBackend()
        self._index: int | None = None
        self._last = GamepadState()
        self._ticks = 0
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def available(self) -> bool:
        return self._backend.available

    @property
    def connected(self) -> bool:
        return self._index is not None

    def start(self) -> None:
        if self._backend.available:
            self._ticks = RESCAN_TICKS  # scan immediately
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._index is not None:
            self._index = None
            self.state_changed.emit(GamepadState())
            self.connection_changed.emit(False, "")

    def _tick(self) -> None:
        if self._index is None:
            # Polling empty XInput slots is slow, so only rescan now and then.
            self._ticks += 1
            if self._ticks < RESCAN_TICKS:
                return
            self._ticks = 0
            for index in range(4):
                state = self._backend.read(index)
                if state is not None:
                    self._index = index
                    self._last = GamepadState(index=index)
                    self.connection_changed.emit(True, f"XInput #{index + 1}")
                    self._publish(state)
                    return
            return

        state = self._backend.read(self._index)
        if state is None:
            self._index = None
            self._last = GamepadState()
            self.state_changed.emit(self._last)
            self.connection_changed.emit(False, "")
            return
        self._publish(state)

    def _publish(self, state: GamepadState) -> None:
        newly = state.buttons & ~self._last.buttons
        if state != self._last:
            self._last = state
            self.state_changed.emit(state)
        for name, mask in BUTTON_MASKS.items():
            if newly & mask:
                self.button_pressed.emit(name)
