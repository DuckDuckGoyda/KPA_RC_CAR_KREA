"""Executes a flattened macro against the drive controller and the link.

Any manual input, an emergency stop, a closed port or a lost link aborts
the run; whichever way a run ends, the car is told to stop.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from ..control.macro import DriveStep, FlatStep, RawStep, SendStep, WaitStep, total_duration
from .drive_controller import DriveController
from .link import LinkManager, LinkState, NotConnectedError

TICK_MS = 10


class MacroRunner(QObject):
    started = pyqtSignal(str)  # macro name
    step_started = pyqtSignal(int, object)  # index, FlatStep
    progress = pyqtSignal(float, float)  # elapsed s, total s
    finished = pyqtSignal(bool, str)  # completed normally, message

    def __init__(self, link: LinkManager, drive: DriveController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._link = link
        self._drive = drive
        self._steps: list[FlatStep] = []
        self._index = -1
        self._step_end = 0.0
        self._started_at = 0.0
        self._total = 0.0
        self._running = False
        self._name = ""

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

        drive.user_input.connect(lambda: self.abort("прерван вручную"))
        drive.emergency.connect(lambda: self.abort("аварийная остановка"))
        link.state_changed.connect(self._on_link_state)

    @property
    def running(self) -> bool:
        return self._running

    def run(self, steps: list[FlatStep], name: str = "") -> None:
        if self._running:
            raise RuntimeError("макрос уже выполняется")
        if not self._link.is_open:
            raise NotConnectedError()
        self._steps = list(steps)
        self._index = -1
        self._name = name
        self._total = total_duration(self._steps)
        self._started_at = time.monotonic()
        self._running = True
        self.started.emit(name)
        self._timer.start()
        self._advance()

    def abort(self, reason: str = "остановлен") -> None:
        if self._running:
            self._finish(False, reason)

    # ------------------------------------------------------------------ internals

    def _advance(self) -> None:
        settings = self._link.profile.drive
        while self._running:
            self._index += 1
            if self._index >= len(self._steps):
                self._finish(True, "выполнен")
                return
            step = self._steps[self._index]
            self.step_started.emit(self._index, step)
            now = time.monotonic()

            if isinstance(step, DriveStep):
                if step.action == "forward":
                    self._drive.macro_drive(step.speed, step.angle)
                elif step.action == "backward":
                    self._drive.macro_drive(-step.speed, step.angle)
                elif step.action == "stop":
                    self._drive.macro_drive(0.0, 0.0)
                else:
                    self._drive.macro_fixed(settings.brake)
                self._step_end = now + step.duration
                if step.duration > 0:
                    return
            elif isinstance(step, WaitStep):
                self._drive.macro_idle()
                self._step_end = now + step.duration
                if step.duration > 0:
                    return
            elif isinstance(step, SendStep):
                self._drive.macro_idle()
                try:
                    self._link.send_command(step.command, "macro")
                except (ValueError, NotConnectedError) as exc:
                    self._finish(False, f"строка {step.line}: {exc}")
                    return
            elif isinstance(step, RawStep):
                self._drive.macro_idle()
                try:
                    self._link.send_raw(step.data, "macro")
                except NotConnectedError as exc:
                    self._finish(False, str(exc))
                    return

    def _tick(self) -> None:
        if not self._running:
            return
        now = time.monotonic()
        self.progress.emit(min(now - self._started_at, self._total), self._total)
        if now >= self._step_end:
            self._advance()

    def _finish(self, ok: bool, message: str) -> None:
        self._running = False
        self._timer.stop()
        self._drive.macro_release()
        self.finished.emit(ok, message)

    def _on_link_state(self, state: LinkState) -> None:
        if state == LinkState.CLOSED:
            self.abort("порт закрыт")
        elif state == LinkState.LOST:
            self.abort("связь потеряна")
