import pytest

from rc_controller.protocol.framing import BadFrame, Frame, FrameParser, Junk, PacketFormat

FMT = PacketFormat()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_encode_matches_worked_examples():
    # Worked examples from docs/protocol/PROTOCOL_NOTES.md.
    assert FMT.encode(0, bytes([0x04])).hex(" ").upper() == "AC 53 04 00 01 04 AB"
    assert FMT.encode(0, bytes([0x03, 50, (-20) & 0xFF])).hex(" ").upper() == "AC 53 06 00 01 03 32 EC 2F"
    # Same vectors as docs/protocol/crc8.c, the firmware reference.
    assert FMT.encode(0, bytes([0x00])).hex(" ").upper() == "AC 53 04 00 01 00 CA"


def test_encode_length_limits():
    with pytest.raises(ValueError):
        FMT.encode(0, b"")  # LEN would be 3, below the minimum of 4
    FMT.encode(0, bytes(FMT.max_payload))
    with pytest.raises(ValueError):
        FMT.encode(0, bytes(FMT.max_payload + 1))


def test_parse_roundtrip_byte_by_byte():
    packet = FMT.encode(7, bytes([0x00, 1, 2, 3]))
    parser = FrameParser(FMT)
    events = []
    for b in packet:
        events += parser.feed(bytes([b]))
    assert len(events) == 1
    frame = events[0]
    assert isinstance(frame, Frame)
    assert (frame.sqn, frame.address, frame.payload, frame.raw) == (7, 1, bytes([0, 1, 2, 3]), packet)


def test_junk_around_frames():
    a = FMT.encode(1, b"\x00")
    b = FMT.encode(2, b"\x00\x10")
    parser = FrameParser(FMT)
    events = parser.feed(b"OK\r\n" + a + b"xyz" + b)
    kinds = [type(e) for e in events]
    assert kinds == [Junk, Frame, Junk, Frame]
    assert events[0].data == b"OK\r\n"
    assert events[2].data == b"xyz"


def test_split_sync_word_is_kept():
    packet = FMT.encode(3, b"\x00")
    parser = FrameParser(FMT)
    assert parser.feed(b"hello\xac") == [Junk(b"hello")]
    events = parser.feed(packet[1:])
    assert len(events) == 1 and isinstance(events[0], Frame)


def test_crc_error_then_resync():
    good = FMT.encode(5, b"\x00")
    broken = bytearray(FMT.encode(4, b"\x00"))
    broken[-1] ^= 0xFF
    parser = FrameParser(FMT)
    events = parser.feed(bytes(broken) + good)
    assert isinstance(events[0], BadFrame) and events[0].reason == "crc"
    frames = [e for e in events if isinstance(e, Frame)]
    assert len(frames) == 1 and frames[0].sqn == 5


def test_bad_length_resyncs():
    good = FMT.encode(9, b"\x00")
    parser = FrameParser(FMT)
    events = parser.feed(b"\xac\x53\x01" + good)
    assert isinstance(events[0], BadFrame) and events[0].reason == "length"
    assert any(isinstance(e, Frame) and e.sqn == 9 for e in events)


def test_stale_partial_frame_is_dropped():
    clock = FakeClock()
    parser = FrameParser(FMT, frame_timeout=0.1, clock=clock)
    packet = FMT.encode(1, b"\x00")
    assert parser.feed(packet[:4]) == []
    clock.now = 0.05
    assert parser.flush_stale() == []
    clock.now = 0.2
    stale = parser.flush_stale()
    assert len(stale) == 1 and isinstance(stale[0], BadFrame) and stale[0].reason == "incomplete"
    # The parser is clean again and takes the next packet normally.
    assert isinstance(parser.feed(packet)[0], Frame)


def test_custom_sync_word():
    fmt = PacketFormat(sync=b"\x55")
    parser = FrameParser(fmt)
    events = parser.feed(fmt.encode(0, b"\x01"))
    assert isinstance(events[0], Frame)
