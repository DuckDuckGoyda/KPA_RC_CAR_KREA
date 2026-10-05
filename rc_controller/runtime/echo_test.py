"""UART echo test: send a block, expect exactly the same bytes back.

This is the professor's current requirement, and it doubles as the way to
measure the real round-trip time of the USB or Bluetooth link. While it runs,
packet traffic (keepalive, driving) is paused.
"""

from __future__ import annotations

import random
import string
import time
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .link import LinkManager, NotConnectedError
from .transport import SerialTransport


@dataclass
class EchoStats:
    total: int = 0
    sent: int = 0
    ok: int = 0
    mismatched: int = 0
    lost: int = 0
    rtts_ms: list[float] = field(default_factory=list)

    @property
    def rtt_min(self) -> float | None:
        return min(self.rtts_ms) if self.rtts_ms else None

    @property
    def rtt_max(self) -> float | None:
        return max(self.rtts_ms) if self.rtts_ms else None

    @property
    def rtt_avg(self) -> float | None:
        return sum(self.rtts_ms) / len(self.rtts_ms) if self.rtts_ms else None


def make_payload(index: int, size: int, text: bool, rng: random.Random | None = None) -> bytes:
    rng = rng or random.Random()
    if not text:
        return rng.randbytes(size)
    head = f"ECHO{index:05d} "
    body_len = max(0, size - len(head) - 1)
    body = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(body_len))
    return (head + body)[: max(1, size - 1)].encode("ascii") + b"\n"


class EchoTester(QObject):
    sample = pyqtSignal(int, str, str)  # index, result ("ok" | "mismatch" | "lost"), detail
    progress = pyqtSignal(object)  # EchoStats
    finished = pyqtSignal(object, str)  # EchoStats, message

    def __init__(self, transport: SerialTransport, link: LinkManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._transport = transport
        self._link = link
        self._running = False
        self._stats = EchoStats()
        self._expected = b""
        self._buffer = bytearray()
        self._sent_at = 0.0
        self._index = 0
        self._interval_ms = 0
        self._size = 0
        self._text = False
        self._rng = random.Random()

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._next = QTimer(self)
        self._next.setSingleShot(True)
        self._next.timeout.connect(self._send_next)
        transport.closed.connect(lambda _reason: self.stop("порт закрыт"))

    @property
    def running(self) -> bool:
        return self._running

    def start(self, count: int, size: int, interval_ms: int, timeout_ms: int, text: bool) -> None:
        if self._running:
            return
        if not self._transport.is_open:
            raise NotConnectedError()
        self._stats = EchoStats(total=count)
        self._size = size
        self._interval_ms = interval_ms
        self._text = text
        self._index = 0
        self._timeout.setInterval(timeout_ms)
        self._running = True
        self._link.set_paused(True)
        self._transport.data_received.connect(self._on_data)
        self._send_next()

    def stop(self, message: str = "остановлен") -> None:
        if not self._running:
            return
        self._running = False
        self._timeout.stop()
        self._next.stop()
        try:
            self._transport.data_received.disconnect(self._on_data)
        except TypeError:
            pass
        self._link.set_paused(False)
        self.finished.emit(self._stats, message)

    def _send_next(self) -> None:
        if not self._running:
            return
        if self._index >= self._stats.total:
            self.stop("завершён")
            return
        self._index += 1
        self._expected = make_payload(self._index, self._size, self._text, self._rng)
        self._buffer.clear()
        self._sent_at = time.monotonic()
        try:
            self._link.send_raw(self._expected, "echo")
        except NotConnectedError:
            self.stop("порт закрыт")
            return
        self._stats.sent += 1
        self._timeout.start()

    def _on_data(self, chunk: bytes) -> None:
        if not self._running or not self._timeout.isActive():
            return  # stray bytes between blocks, e.g. a late echo
        self._buffer += chunk
        if len(self._buffer) < len(self._expected):
            return
        self._timeout.stop()
        rtt = (time.monotonic() - self._sent_at) * 1000
        got = bytes(self._buffer[: len(self._expected)])
        if got == self._expected:
            self._stats.ok += 1
            self._stats.rtts_ms.append(rtt)
            self.sample.emit(self._index, "ok", f"{rtt:.1f} мс")
        else:
            self._stats.mismatched += 1
            diff = next(i for i, (a, b) in enumerate(zip(got, self._expected, strict=True)) if a != b)
            self.sample.emit(self._index, "mismatch", f"расхождение с байта {diff + 1}")
        self._finish_sample()

    def _on_timeout(self) -> None:
        if not self._running:
            return
        if self._buffer:
            self._stats.mismatched += 1
            self.sample.emit(self._index, "mismatch", f"получено {len(self._buffer)} из {len(self._expected)} байт")
        else:
            self._stats.lost += 1
            self.sample.emit(self._index, "lost", "нет ответа")
        self._finish_sample()

    def _finish_sample(self) -> None:
        self.progress.emit(self._stats)
        self._next.start(self._interval_ms)
