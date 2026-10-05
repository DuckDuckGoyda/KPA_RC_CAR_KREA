"""Request/response link on top of the serial transport.

Implements the exchange rules of the protocol:

* one request in flight at a time; the reply is matched by SQN;
* a request without a reply is retried (default 3 times, 1 s apart), each
  retry with a fresh SQN as the spec requires;
* several failed exchanges in a row mean the link is lost;
* periodic keepalive / telemetry request.

On top of that, drive commands are *coalesced*: a newer drive command replaces
a queued older one, and a failed drive command is never retried once a newer
one exists, so the car never receives stale speeds.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..ports import KIND_LABELS, PortInfo
from ..protocol.commands import EncodedCommand, decode_reply, describe_payload, encode_command
from ..protocol.framing import BadFrame, Frame, FrameParser, Junk, hex_bytes
from ..protocol.profile import Profile
from .transport import SerialTransport

RETRYABLE_ACKS = {2}  # "CRC error" on the car side: the packet got mangled, send again


class LinkState(Enum):
    CLOSED = "closed"
    CONNECTING = "connecting"
    OPEN = "open"  # port is open, nothing heard back yet
    OK = "ok"
    LOST = "lost"


class NotConnectedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("порт не открыт — сначала подключитесь")


@dataclass
class LogEntry:
    direction: str  # "tx" | "rx" | "event"
    source: str  # terminal | drive | keepalive | macro | stop | echo | link
    kind: str  # packet | raw | junk | bad | event
    text: str
    data: bytes = b""
    sqn: int | None = None
    status: str = ""
    rtt_ms: float | None = None
    level: str = "info"  # info | ok | warn | error
    time: datetime = field(default_factory=datetime.now)


@dataclass
class LinkStats:
    tx_packets: int = 0
    rx_packets: int = 0
    tx_bytes: int = 0
    rx_bytes: int = 0
    timeouts: int = 0
    bad_frames: int = 0
    nacks: int = 0
    consecutive_failures: int = 0
    last_rtt_ms: float | None = None
    avg_rtt_ms: float | None = None


@dataclass(eq=False)
class _Request:
    command: EncodedCommand
    source: str
    retries_left: int
    coalesce: str | None
    priority: bool
    not_before: float = 0.0
    attempt: int = 0
    sqn: int = -1
    frame: bytes = b""
    sent_at: float = 0.0


def preview_bytes(data: bytes, limit: int = 64) -> str:
    """Show text as text and binary as hex, whichever reads better."""
    clipped = data[:limit]
    printable = sum(32 <= b < 127 or b in (9, 10, 13) for b in clipped)
    more = f" … (+{len(data) - limit} байт)" if len(data) > limit else ""
    if clipped and printable / len(clipped) >= 0.8:
        text = clipped.decode("ascii", errors="replace")
        text = text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
        return f'"{text}"{more}'
    return hex_bytes(clipped) + more


class LinkManager(QObject):
    state_changed = pyqtSignal(object)  # LinkState
    log_entry = pyqtSignal(object)  # LogEntry
    stats_changed = pyqtSignal(object)  # LinkStats

    def __init__(self, transport: SerialTransport, profile: Profile, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._transport = transport
        self._profile = profile
        self._parser = self._make_parser()
        self._queue: list[_Request] = []
        self._in_flight: _Request | None = None
        self._recent_timeouts: dict[int, _Request] = {}
        self._recent_sent: deque[bytes] = deque(maxlen=32)
        self._sqn = 0
        self._paused = False
        self._keepalive_enabled = profile.link.keepalive_enabled
        self._state = LinkState.CLOSED
        self.stats = LinkStats()

        self._reply_timer = QTimer(self)
        self._reply_timer.setSingleShot(True)
        self._reply_timer.timeout.connect(self._on_reply_timeout)
        self._wake_timer = QTimer(self)
        self._wake_timer.setSingleShot(True)
        self._wake_timer.timeout.connect(self._pump)
        self._keepalive_timer = QTimer(self)
        self._keepalive_timer.timeout.connect(self._on_keepalive)
        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(50)
        self._stale_timer.timeout.connect(self._on_stale_check)

        transport.opening.connect(self._on_opening)
        transport.opened.connect(self._on_opened)
        transport.open_failed.connect(self._on_open_failed)
        transport.closed.connect(self._on_closed)
        transport.data_received.connect(self._on_data)

    # ------------------------------------------------------------------ properties

    @property
    def profile(self) -> Profile:
        return self._profile

    @property
    def state(self) -> LinkState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._transport.is_open

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def keepalive_enabled(self) -> bool:
        return self._keepalive_enabled

    # ------------------------------------------------------------------ public API

    def set_profile(self, profile: Profile) -> None:
        self._profile = profile
        self._parser = self._make_parser()
        self._drop_all()
        self._keepalive_timer.setInterval(profile.link.keepalive_period_ms)

    def set_keepalive_enabled(self, enabled: bool) -> None:
        self._keepalive_enabled = enabled
        if enabled and self.is_open:
            self._on_keepalive()

    def set_paused(self, paused: bool) -> None:
        """Pause all packet traffic and parsing (used by the echo test)."""
        if paused == self._paused:
            return
        self._paused = paused
        self._parser.reset()
        if paused:
            self._drop_all()
        else:
            self._schedule_pump()

    def send_command(
        self,
        text: str,
        source: str = "terminal",
        *,
        coalesce: str | None = None,
        priority: bool = False,
        retries: int | None = None,
    ) -> EncodedCommand:
        """Queue a command line. Raises CommandError or NotConnectedError."""
        command = encode_command(self._profile, text)
        if not self.is_open:
            raise NotConnectedError()
        if retries is None:
            retries = self._profile.link.retries
        self._enqueue(_Request(command, source, retries, coalesce, priority))
        return command

    def send_raw(self, data: bytes, source: str = "terminal") -> None:
        if not self.is_open:
            raise NotConnectedError()
        if self._transport.write(data):
            self.stats.tx_bytes += len(data)
            self._log("tx", source, "raw", preview_bytes(data), data=data)
            self.stats_changed.emit(self.stats)

    def emergency_stop(self) -> None:
        """Drop everything queued and send the profile's stop commands first."""
        self._queue.clear()
        if self._in_flight is not None:
            self._in_flight.retries_left = 0
        if not self.is_open:
            return
        self._log("event", "stop", "event", "аварийная остановка", level="warn")
        for i, template in enumerate(self._profile.drive.stop):
            try:
                self.send_command(template, "stop", coalesce=f"stop{i}", priority=True)
            except (ValueError, NotConnectedError) as exc:  # profile was validated; belt and braces
                self._log("event", "stop", "event", f"не удалось отправить стоп: {exc}", level="error")

    def log_event(self, text: str, source: str = "link", level: str = "info") -> None:
        """Put an application event (macro started, echo test result, ...) into the log."""
        self._log("event", source, "event", text, level=level)

    def has_pending(self, source: str) -> bool:
        if self._in_flight is not None and self._in_flight.source == source:
            return True
        return any(r.source == source for r in self._queue)

    # ------------------------------------------------------------------ queue

    def _enqueue(self, request: _Request) -> None:
        if request.coalesce is not None:
            self._queue = [r for r in self._queue if r.coalesce != request.coalesce]
        self._insert(request)
        self._schedule_pump()

    def _insert(self, request: _Request) -> None:
        if request.priority:
            index = sum(1 for r in self._queue if r.priority)
            self._queue.insert(index, request)
        else:
            self._queue.append(request)

    def _retry_later(self, request: _Request) -> None:
        request.retries_left -= 1
        request.not_before = time.monotonic() + self._profile.link.retry_interval_ms / 1000
        self._insert(request)

    def _schedule_pump(self) -> None:
        # Coalesce bursts of enqueues into one pump on the next event-loop pass.
        if not self._wake_timer.isActive() or self._wake_timer.remainingTime() > 0:
            self._wake_timer.start(0)

    def _pump(self) -> None:
        while self.is_open and not self._paused and self._in_flight is None and self._queue:
            now = time.monotonic()
            ready = next((r for r in self._queue if r.not_before <= now), None)
            if ready is None:
                wait_ms = min(r.not_before for r in self._queue) - now
                self._wake_timer.start(max(1, int(wait_ms * 1000) + 1))
                return
            self._queue.remove(ready)
            self._transmit(ready)

    def _transmit(self, request: _Request) -> None:
        packet = self._profile.packet
        sqn = self._sqn
        self._sqn = (self._sqn + 1) % packet.sqn_modulo
        frame = packet.encode(sqn, request.command.payload)
        request.sqn = sqn
        request.frame = frame
        request.attempt += 1
        request.sent_at = time.monotonic()
        if not self._transport.write(frame):
            return  # transport closed itself; _on_closed cleans up
        self.stats.tx_packets += 1
        self.stats.tx_bytes += len(frame)
        self._recent_sent.append(frame)
        status = f"повтор {request.attempt - 1}" if request.attempt > 1 else ""
        self._log("tx", request.source, "packet", request.command.text, data=frame, sqn=sqn, status=status)
        if self._profile.link.wait_for_reply:
            self._in_flight = request
            self._reply_timer.start(self._profile.link.reply_timeout_ms)
        self.stats_changed.emit(self.stats)

    def _drop_all(self) -> None:
        self._queue.clear()
        self._in_flight = None
        self._reply_timer.stop()
        self._wake_timer.stop()

    def _superseded(self, request: _Request) -> bool:
        return request.coalesce is not None and any(r.coalesce == request.coalesce for r in self._queue)

    # ------------------------------------------------------------------ receive

    def _on_data(self, chunk: bytes) -> None:
        self.stats.rx_bytes += len(chunk)
        if not self._paused:
            for event in self._parser.feed(chunk):
                self._handle_event(event)
        self.stats_changed.emit(self.stats)

    def _on_stale_check(self) -> None:
        for event in self._parser.flush_stale():
            self._handle_event(event)

    def _handle_event(self, event: Frame | BadFrame | Junk) -> None:
        if isinstance(event, Frame):
            self._on_frame(event)
        elif isinstance(event, BadFrame):
            self.stats.bad_frames += 1
            if event.reason == "crc":
                text = f"ошибка CRC: в пакете 0x{event.raw[-1]:02X}, расчёт 0x{event.expected_crc:02X}"
            elif event.reason == "length":
                text = f"недопустимое значение LEN = {event.raw[-1]}"
            else:
                text = "пакет оборвался (не хватает байт)"
            self._log("rx", "link", "bad", text, data=event.raw, level="error")
        else:
            self._log("rx", "link", "junk", preview_bytes(event.data), data=event.data)

    def _on_frame(self, frame: Frame) -> None:
        self.stats.rx_packets += 1
        now = time.monotonic()
        request = self._in_flight

        if request is not None and frame.sqn == request.sqn:
            self._reply_timer.stop()
            self._in_flight = None
            rtt = (now - request.sent_at) * 1000
            if frame.raw == request.frame:
                self._log(
                    "rx",
                    request.source,
                    "packet",
                    f"эхо: {describe_payload(self._profile, frame.payload)}",
                    data=frame.raw,
                    sqn=frame.sqn,
                    status="эхо",
                    rtt_ms=rtt,
                    level="ok",
                )
                self._mark_alive(rtt)
            else:
                info = decode_reply(self._profile, request.command.spec, frame.payload)
                level = "ok" if info.ok else "error"
                if not info.ok:
                    self.stats.nacks += 1
                self._log(
                    "rx",
                    request.source,
                    "packet",
                    f"{request.command.name} → {info.summary()}",
                    data=frame.raw,
                    sqn=frame.sqn,
                    status=info.ack_text if info.ack is not None else "пусто",
                    rtt_ms=rtt,
                    level=level,
                )
                self._mark_alive(rtt)
                if info.ack in RETRYABLE_ACKS and request.retries_left > 0 and not self._superseded(request):
                    self._retry_later(request)
            self._schedule_pump()
            return

        late = self._recent_timeouts.pop(frame.sqn, None)
        if late is not None:
            rtt = (now - late.sent_at) * 1000
            info = decode_reply(self._profile, late.command.spec, frame.payload)
            text = "эхо" if frame.raw == late.frame else info.summary()
            self._log(
                "rx",
                late.source,
                "packet",
                f"опоздавший ответ на {late.command.name}: {text}",
                data=frame.raw,
                sqn=frame.sqn,
                status="опоздал",
                rtt_ms=rtt,
                level="warn",
            )
            self._mark_alive(None)
        elif frame.raw in self._recent_sent:
            self._log(
                "rx",
                "link",
                "packet",
                f"эхо: {describe_payload(self._profile, frame.payload)}",
                data=frame.raw,
                sqn=frame.sqn,
                status="эхо",
                level="ok",
            )
            self._mark_alive(None)
        else:
            info = decode_reply(self._profile, None, frame.payload)
            self._log(
                "rx",
                "link",
                "packet",
                f"пакет без запроса: {info.summary()}",
                data=frame.raw,
                sqn=frame.sqn,
                status="без запроса",
                level="warn",
            )

    def _on_reply_timeout(self) -> None:
        request = self._in_flight
        if request is None:
            return
        self._in_flight = None
        self.stats.timeouts += 1
        self.stats.consecutive_failures += 1
        self._recent_timeouts[request.sqn] = request
        while len(self._recent_timeouts) > 32:
            self._recent_timeouts.pop(next(iter(self._recent_timeouts)))

        link = self._profile.link
        retry = request.retries_left > 0 and not self._superseded(request)
        tail = f", повтор через {link.retry_interval_ms} мс" if retry else ""
        self._log(
            "event",
            request.source,
            "event",
            f"нет ответа на {request.command.name} за {link.reply_timeout_ms} мс{tail}",
            sqn=request.sqn,
            status="таймаут",
            level="warn",
        )
        if retry:
            self._retry_later(request)
        if self.stats.consecutive_failures >= link.lost_after_failures and self._state != LinkState.LOST:
            self._set_state(LinkState.LOST)
            self._log("event", "link", "event", "связь потеряна: машинка не отвечает", level="error")
        self.stats_changed.emit(self.stats)
        self._schedule_pump()

    def _mark_alive(self, rtt: float | None) -> None:
        self.stats.consecutive_failures = 0
        if rtt is not None:
            self.stats.last_rtt_ms = rtt
            avg = self.stats.avg_rtt_ms
            self.stats.avg_rtt_ms = rtt if avg is None else avg * 0.8 + rtt * 0.2
        if self._state != LinkState.OK:
            if self._state == LinkState.LOST:
                self._log("event", "link", "event", "связь восстановлена", level="ok")
            self._set_state(LinkState.OK)

    # ------------------------------------------------------------------ keepalive

    def _on_keepalive(self) -> None:
        command = self._profile.link.keepalive_command
        if not (self.is_open and self._keepalive_enabled and command and not self._paused):
            return
        if self.has_pending("keepalive"):
            return
        try:
            self.send_command(command, "keepalive", coalesce="keepalive", retries=0)
        except (ValueError, NotConnectedError):
            pass

    # ------------------------------------------------------------------ transport events

    def _on_opening(self, port: PortInfo) -> None:
        self._set_state(LinkState.CONNECTING)
        self._log("event", "link", "event", f"подключение к {port.device} ({KIND_LABELS[port.kind]})…")

    def _on_opened(self, port: PortInfo) -> None:
        self._sqn = 0  # spec: SQN starts from 0 at the beginning of a session
        self._parser = self._make_parser()
        self._recent_timeouts.clear()
        self._recent_sent.clear()
        self.stats = LinkStats()
        self._set_state(LinkState.OPEN)
        self._log("event", "link", "event", f"порт {port.device} открыт ({KIND_LABELS[port.kind]})", level="ok")
        self._keepalive_timer.start(self._profile.link.keepalive_period_ms)
        self._stale_timer.start()
        self.stats_changed.emit(self.stats)

    def _on_open_failed(self, message: str) -> None:
        self._set_state(LinkState.CLOSED)
        self._log("event", "link", "event", f"не удалось открыть порт: {message}", level="error")

    def _on_closed(self, reason: str) -> None:
        self._drop_all()
        self._keepalive_timer.stop()
        self._stale_timer.stop()
        self._parser.reset()
        self._set_state(LinkState.CLOSED)
        text = f"порт закрыт: {reason}" if reason else "порт закрыт"
        self._log("event", "link", "event", text, level="error" if reason else "info")

    # ------------------------------------------------------------------ helpers

    def _make_parser(self) -> FrameParser:
        return FrameParser(self._profile.packet, frame_timeout=self._profile.link.frame_timeout_ms / 1000)

    def _set_state(self, state: LinkState) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def _log(self, direction: str, source: str, kind: str, text: str, **kwargs: object) -> None:
        self.log_entry.emit(LogEntry(direction, source, kind, text, **kwargs))  # type: ignore[arg-type]
