"""Turns keyboard / gamepad / macro input into drive commands on the link.

Priority: a running macro > held keyboard keys > gamepad. Any human input
while a macro runs raises ``user_input`` so the macro can abort itself.

Send policy: a drive command goes out when its rendered text changes, but no
more often than every ``min_interval_ms`` (a ramp changes the values on every
tick; only the latest one is sent). A stop (all zeros) is never held back.
While the car is moving the command is repeated every ``resend_ms`` (so a
firmware watchdog keeps getting fed). When idle nothing is sent; keepalive
covers the link.

Keyboard and gamepad both go through the ramp; the user's sensitivity setting
scales the profile's ramp rates for throttle and steering separately.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..control.drive import DriveModel, DriveValues, mix
from ..protocol.commands import CommandError, render_template
from ..protocol.profile import Profile
from .link import LinkManager, LinkState, NotConnectedError

ACTIONS = ("forward", "backward", "left", "right")
TICK_MS = 20
GAMEPAD_ACTIVE = 0.02


@dataclass(frozen=True)
class DriveOutput:
    values: DriveValues
    source: str  # "idle" | "keyboard" | "gamepad" | "macro"


@dataclass
class _MacroCommand:
    mode: str  # "drive" | "fixed" | "idle"
    throttle: float = 0.0
    steer: float = 0.0
    lines: tuple[str, ...] = ()


class DriveController(QObject):
    output_changed = pyqtSignal(object)  # DriveOutput
    keys_changed = pyqtSignal(object)  # frozenset of held actions
    user_input = pyqtSignal()  # a human touched the controls
    emergency = pyqtSignal()  # emergency stop was triggered

    def __init__(self, link: LinkManager, profile: Profile, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._link = link
        self._profile = profile
        self._model = DriveModel.from_settings(profile.drive)
        self._held: set[str] = set()
        self._gamepad = (0.0, 0.0)
        self._gamepad_latched = False
        self._speed_limit = 1.0
        self._throttle_gain = 1.0
        self._steer_gain = 1.0
        self._macro: _MacroCommand | None = None
        self._output = DriveOutput(DriveValues(), "idle")
        self._last_source = "idle"
        self._last_lines: tuple[str, ...] | None = self._zero_lines()
        self._last_send = 0.0
        self._last_tick = time.monotonic()

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        link.state_changed.connect(self._on_link_state)

    # ------------------------------------------------------------------ properties

    @property
    def output(self) -> DriveOutput:
        return self._output

    @property
    def held(self) -> frozenset[str]:
        return frozenset(self._held)

    @property
    def speed_limit(self) -> float:
        return self._speed_limit

    # ------------------------------------------------------------------ inputs

    def set_profile(self, profile: Profile) -> None:
        self._profile = profile
        self._model = DriveModel.from_settings(profile.drive, self._throttle_gain, self._steer_gain)
        self._last_lines = self._zero_lines()

    def press(self, action: str) -> None:
        if action == "stop":
            self.emergency_stop()
            return
        if action in ACTIONS and action not in self._held:
            self._held.add(action)
            self.keys_changed.emit(self.held)
            self.user_input.emit()

    def release(self, action: str) -> None:
        if action in self._held:
            self._held.discard(action)
            self.keys_changed.emit(self.held)

    def release_all(self) -> None:
        if self._held:
            self._held.clear()
            self.keys_changed.emit(self.held)

    def set_gamepad(self, throttle: float, steer: float) -> None:
        was_active = max(abs(self._gamepad[0]), abs(self._gamepad[1])) > GAMEPAD_ACTIVE
        self._gamepad = (throttle, steer)
        active = max(abs(throttle), abs(steer)) > GAMEPAD_ACTIVE
        if self._gamepad_latched:
            if not active:
                self._gamepad_latched = False  # stick back at neutral: accept input again
            return
        if active and not was_active:
            self.user_input.emit()

    def set_speed_limit(self, fraction: float) -> None:
        self._speed_limit = max(0.0, min(1.0, fraction))

    def set_sensitivity(self, throttle: float, steer: float) -> None:
        """Multipliers for the profile's ramp rates (1.0 = as in the profile)."""
        self._throttle_gain = max(0.01, throttle)
        self._steer_gain = max(0.01, steer)
        self._model.configure(self._profile.drive, self._throttle_gain, self._steer_gain)

    def emergency_stop(self) -> None:
        self.release_all()
        self._model.reset()
        self._macro = None
        # Keep ignoring a gamepad that is still deflected until it is released.
        self._gamepad_latched = max(abs(self._gamepad[0]), abs(self._gamepad[1])) > GAMEPAD_ACTIVE
        self._last_lines = self._zero_lines()
        self._link.emergency_stop()
        self._set_output(DriveOutput(DriveValues(), "idle"))
        self.emergency.emit()

    # ------------------------------------------------------------------ macro API

    def macro_drive(self, throttle: float, steer: float) -> None:
        self._macro = _MacroCommand("drive", throttle, steer)

    def macro_fixed(self, lines: tuple[str, ...]) -> None:
        self._macro = _MacroCommand("fixed", lines=lines)

    def macro_idle(self) -> None:
        self._macro = _MacroCommand("idle")

    def macro_release(self) -> None:
        """End of macro: return control to the user and make sure the car stops."""
        if self._macro is None:
            return
        self._macro = None
        self._model.reset()
        self._last_lines = None  # forces the zero command out on the next tick

    # ------------------------------------------------------------------ loop

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.1)
        self._last_tick = now
        settings = self._profile.drive

        lines: tuple[str, ...] | None
        if self._macro is not None:
            macro = self._macro
            if macro.mode == "drive":
                self._model.set(macro.throttle, macro.steer)
                values = mix(self._model.throttle, self._model.steer, settings, self._speed_limit)
                lines = self._render(values)
                active = not values.is_zero
            elif macro.mode == "fixed":
                values, lines, active = DriveValues(), macro.lines, True
            else:
                values, lines, active = DriveValues(), None, False
            source = "macro"
        else:
            if self._held:
                target_t = ("forward" in self._held) - ("backward" in self._held)
                target_s = ("right" in self._held) - ("left" in self._held)
                self._model.update(target_t, target_s, dt)
                source = "keyboard"
            elif self._gamepad_active() or self._last_source == "gamepad":
                # Ramp toward the stick, and back to neutral once it is released.
                target = self._gamepad if self._gamepad_active() else (0.0, 0.0)
                self._model.update(*target, dt)
                source = "gamepad" if not self._model.idle else "idle"
            else:
                self._model.update(0.0, 0.0, dt)  # keyboard released: ramp down
                source = "keyboard" if not self._model.idle else "idle"
            values = mix(self._model.throttle, self._model.steer, settings, self._speed_limit)
            lines = self._render(values)
            active = not values.is_zero

        self._last_source = source
        self._set_output(DriveOutput(values, source if not values.is_zero or source == "macro" else "idle"))
        if lines is not None:
            self._maybe_send(lines, active, values.is_zero, now, "macro" if self._macro is not None else "drive")

    def _maybe_send(self, lines: tuple[str, ...], active: bool, stopping: bool, now: float, source: str) -> None:
        if not self._link.is_open or self._link.paused:
            return
        settings = self._profile.drive
        elapsed_ms = (now - self._last_send) * 1000
        if lines != self._last_lines:
            ready = stopping or elapsed_ms >= settings.min_interval_ms
        else:
            ready = active and elapsed_ms >= settings.resend_ms
        if not ready:
            return
        for i, line in enumerate(lines):
            try:
                self._link.send_command(line, source, coalesce=f"drive{i}", retries=0)
            except (CommandError, NotConnectedError):
                return
        self._last_lines = lines
        self._last_send = now

    def _gamepad_active(self) -> bool:
        return not self._gamepad_latched and max(map(abs, self._gamepad)) > GAMEPAD_ACTIVE

    def _render(self, values: DriveValues) -> tuple[str, ...]:
        return tuple(render_template(t, values.template_values()) for t in self._profile.drive.command)

    def _zero_lines(self) -> tuple[str, ...]:
        return self._render(DriveValues())

    def _set_output(self, output: DriveOutput) -> None:
        if output != self._output:
            self._output = output
            self.output_changed.emit(output)

    def _on_link_state(self, state: LinkState) -> None:
        if state == LinkState.OPEN:
            # A new session starts from "stopped"; nothing is sent until there is input.
            self._last_lines = self._zero_lines()
