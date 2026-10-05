"""Binary packet framing from the course protocol.

Layout (both directions)::

    SYNC(2) | LEN | SQN | ADDR | PAYLOAD ... | CRC

LEN counts every byte after itself (SQN .. CRC), so a whole packet is
``len(sync) + 1 + LEN`` bytes. The CRC covers LEN .. last payload byte.
In a command packet PAYLOAD is CODE + PARAM; in a reply it is ACK + DATA.
The parser does not care which one it is looking at.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .crc import DEFAULT_CRC, Crc8

HEADER_AFTER_LEN = 2  # SQN + ADDR
CRC_SIZE = 1


@dataclass(frozen=True)
class PacketFormat:
    sync: bytes = b"\xac\x53"
    address: int = 1
    crc: Crc8 = DEFAULT_CRC
    sqn_modulo: int = 255
    min_len: int = 4
    max_len: int = 253

    def __post_init__(self) -> None:
        if not self.sync:
            raise ValueError("sync word must not be empty")
        if not 1 <= self.sqn_modulo <= 256:
            raise ValueError("sqn_modulo must be within 1..256")
        if not 0 <= self.address <= 0xFF:
            raise ValueError("address must fit in one byte")
        if not HEADER_AFTER_LEN + CRC_SIZE <= self.min_len <= self.max_len <= 0xFF:
            raise ValueError("invalid LEN limits")

    @property
    def max_payload(self) -> int:
        return self.max_len - HEADER_AFTER_LEN - CRC_SIZE

    def encode(self, sqn: int, payload: bytes, address: int | None = None) -> bytes:
        length = HEADER_AFTER_LEN + len(payload) + CRC_SIZE
        if length < self.min_len:
            raise ValueError(f"packet too short: LEN={length}, minimum {self.min_len}")
        if length > self.max_len:
            raise ValueError(f"packet too long: LEN={length}, maximum {self.max_len}")
        addr = self.address if address is None else address
        body = bytes([length, sqn & 0xFF, addr & 0xFF]) + bytes(payload)
        return self.sync + body + bytes([self.crc(body)])


@dataclass(frozen=True)
class Frame:
    """A packet with a valid sync word, LEN and CRC."""

    raw: bytes
    length: int
    sqn: int
    address: int
    payload: bytes


@dataclass(frozen=True)
class BadFrame:
    """Something that started with the sync word but failed validation."""

    raw: bytes
    reason: str  # "crc" | "length" | "incomplete"
    expected_crc: int | None = None


@dataclass(frozen=True)
class Junk:
    """Bytes outside any packet (text, AT replies, echoed garbage...)."""

    data: bytes


ParseEvent = Frame | BadFrame | Junk


@dataclass
class FrameParser:
    """Incremental parser: feed it whatever the port delivers, get events back.

    A partial packet that stops growing for ``frame_timeout`` seconds is
    reported as incomplete and the parser resynchronises right after its sync
    word, so a lost byte cannot wedge the stream.
    """

    fmt: PacketFormat = field(default_factory=PacketFormat)
    frame_timeout: float = 0.1
    clock: Callable[[], float] = time.monotonic
    max_buffer: int = 4096

    def __post_init__(self) -> None:
        self._buf = bytearray()
        self._partial_since: float | None = None

    def reset(self) -> None:
        self._buf.clear()
        self._partial_since = None

    @property
    def pending(self) -> bytes:
        return bytes(self._buf)

    def feed(self, data: bytes) -> list[ParseEvent]:
        if data:
            self._buf += data
            self._partial_since = self.clock()
        events = self._scan()
        if len(self._buf) > self.max_buffer:
            events.append(Junk(bytes(self._buf)))
            self.reset()
        return events

    def flush_stale(self) -> list[ParseEvent]:
        """Drop a partial packet that has been waiting too long."""
        if not self._buf or self._partial_since is None:
            return []
        if self.clock() - self._partial_since < self.frame_timeout:
            return []
        sync = self.fmt.sync
        if self._buf.startswith(sync):
            events: list[ParseEvent] = [BadFrame(bytes(self._buf), "incomplete")]
        else:
            events = [Junk(bytes(self._buf))]  # a lone first sync byte, say
        self.reset()
        return events

    def _scan(self) -> list[ParseEvent]:
        events: list[ParseEvent] = []
        buf = self._buf
        sync = self.fmt.sync
        header = len(sync) + 1
        while buf:
            idx = buf.find(sync)
            if idx < 0:
                keep = _sync_prefix_at_end(buf, sync)
                cut = len(buf) - keep
                if cut:
                    events.append(Junk(bytes(buf[:cut])))
                    del buf[:cut]
                break
            if idx:
                events.append(Junk(bytes(buf[:idx])))
                del buf[:idx]
            if len(buf) < header:
                break
            length = buf[len(sync)]
            if not self.fmt.min_len <= length <= self.fmt.max_len:
                events.append(BadFrame(bytes(buf[:header]), "length"))
                del buf[: len(sync)]
                continue
            total = header + length
            if len(buf) < total:
                break
            raw = bytes(buf[:total])
            body = raw[len(sync) : -1]
            crc = self.fmt.crc(body)
            if crc != raw[-1]:
                events.append(BadFrame(raw, "crc", expected_crc=crc))
                # The sync word may have been a coincidence inside other data,
                # so only skip the sync itself and rescan the rest.
                del buf[: len(sync)]
                continue
            events.append(Frame(raw=raw, length=length, sqn=body[1], address=body[2], payload=body[3:]))
            del buf[:total]
        if not buf:
            self._partial_since = None
        return events


def _sync_prefix_at_end(buf: bytearray, sync: bytes) -> int:
    """Length of the longest proper prefix of ``sync`` that ends ``buf``."""
    for size in range(min(len(sync) - 1, len(buf)), 0, -1):
        if buf.endswith(sync[:size]):
            return size
    return 0


def hex_bytes(data: bytes) -> str:
    return data.hex(" ").upper()
