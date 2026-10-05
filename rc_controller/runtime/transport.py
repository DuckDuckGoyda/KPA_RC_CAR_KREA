"""pyserial transport with Qt signals.

Opening happens on a worker thread: on Windows, opening a Bluetooth COM port
makes the PC page the HC-05, which can block for several seconds. Reading
also runs on its own thread; received chunks arrive in the GUI thread through
a queued signal. Writes are done from the GUI thread (pyserial on Windows uses
separate overlapped I/O for reads and writes, so that is safe).
"""

from __future__ import annotations

import threading

import serial
from PyQt6.QtCore import QObject, pyqtSignal

from ..ports import PortInfo, classify, list_ports

READ_TIMEOUT_S = 0.05
WRITE_TIMEOUT_S = 1.0


class SerialTransport(QObject):
    opening = pyqtSignal(object)  # PortInfo
    opened = pyqtSignal(object)  # PortInfo
    open_failed = pyqtSignal(str)
    closed = pyqtSignal(str)  # reason; "" when closed by the user
    data_received = pyqtSignal(bytes)
    data_sent = pyqtSignal(bytes)

    # Internal cross-thread hand-offs.
    _open_done = pyqtSignal(object, object, int)  # serial object, PortInfo, generation
    _open_error = pyqtSignal(str, int)
    _reader_died = pyqtSignal(str, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._serial: serial.SerialBase | None = None
        self._port: PortInfo | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._generation = 0  # invalidates late results from an abandoned open/reader
        self._connecting = False
        self._open_done.connect(self._on_open_done)
        self._open_error.connect(self._on_open_error)
        self._reader_died.connect(self._on_reader_died)

    # ------------------------------------------------------------------ state

    @property
    def is_open(self) -> bool:
        return self._serial is not None

    @property
    def is_connecting(self) -> bool:
        return self._connecting

    @property
    def port(self) -> PortInfo | None:
        return self._port

    # ------------------------------------------------------------------ control

    def open(self, device: str, baudrate: int) -> None:
        if self._serial is not None or self._connecting:
            return
        device = device.strip()
        info = next((p for p in list_ports() if p.device.lower() == device.lower()), None)
        if info is None:
            info = classify(device)
        self._generation += 1
        generation = self._generation
        self._connecting = True
        self._port = info
        self.opening.emit(info)

        def worker() -> None:
            try:
                ser = serial.serial_for_url(
                    device,
                    baudrate=baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=READ_TIMEOUT_S,
                    write_timeout=WRITE_TIMEOUT_S,
                )
            except (serial.SerialException, OSError, ValueError) as exc:
                self._open_error.emit(str(exc), generation)
                return
            self._open_done.emit(ser, info, generation)

        threading.Thread(target=worker, name="serial-open", daemon=True).start()

    def cancel_open(self) -> None:
        """Forget a pending open; if it still succeeds, the port is closed right away."""
        if self._connecting:
            self._generation += 1
            self._connecting = False
            self._port = None
            self.open_failed.emit("подключение отменено")

    def close(self, reason: str = "") -> None:
        if self._connecting and self._serial is None:
            self.cancel_open()
            return
        ser = self._serial
        if ser is None:
            return
        self._generation += 1
        self._stop.set()
        self._serial = None
        try:
            ser.close()
        except (serial.SerialException, OSError):
            pass
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=0.5)
        self._reader = None
        self.closed.emit(reason)
        self._port = None

    def write(self, data: bytes) -> bool:
        ser = self._serial
        if ser is None or not data:
            return False
        try:
            ser.write(data)
        except (serial.SerialException, OSError) as exc:
            self.close(f"ошибка записи: {exc}")
            return False
        self.data_sent.emit(bytes(data))
        return True

    # ------------------------------------------------------------------ internals

    def _on_open_done(self, ser: serial.SerialBase, info: PortInfo, generation: int) -> None:
        if generation != self._generation:
            try:
                ser.close()
            except (serial.SerialException, OSError):
                pass
            return
        self._connecting = False
        self._serial = ser
        self._port = info
        self._stop = threading.Event()
        self._reader = threading.Thread(
            target=self._read_loop, args=(ser, self._stop, generation), name="serial-read", daemon=True
        )
        self._reader.start()
        self.opened.emit(info)

    def _on_open_error(self, message: str, generation: int) -> None:
        if generation != self._generation:
            return
        self._connecting = False
        self._port = None
        self.open_failed.emit(message)

    def _on_reader_died(self, message: str, generation: int) -> None:
        if generation == self._generation and self._serial is not None:
            self.close(f"порт отключился: {message}")

    def _read_loop(self, ser: serial.SerialBase, stop: threading.Event, generation: int) -> None:
        while not stop.is_set():
            try:
                waiting = ser.in_waiting
                chunk = ser.read(waiting or 1)
            except (serial.SerialException, OSError, TypeError, AttributeError) as exc:
                if not stop.is_set():
                    self._reader_died.emit(str(exc) or type(exc).__name__, generation)
                return
            if chunk and not stop.is_set():
                self.data_received.emit(bytes(chunk))
