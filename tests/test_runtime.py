"""Link / drive / macro / echo behaviour, driven through a fake transport and pyserial's loop://."""

from dataclasses import replace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from rc_controller.control.macro import flatten, parse_macro
from rc_controller.ports import PortInfo
from rc_controller.protocol.framing import Frame, FrameParser
from rc_controller.runtime.drive_controller import DriveController
from rc_controller.runtime.echo_test import EchoTester
from rc_controller.runtime.link import LinkManager, LinkState, NotConnectedError
from rc_controller.runtime.macro_runner import MacroRunner
from rc_controller.runtime.transport import SerialTransport


class FakeTransport(QObject):
    opening = pyqtSignal(object)
    opened = pyqtSignal(object)
    open_failed = pyqtSignal(str)
    closed = pyqtSignal(str)
    data_received = pyqtSignal(bytes)
    data_sent = pyqtSignal(bytes)

    def __init__(self):
        super().__init__()
        self.is_open = False
        self.written: list[bytes] = []

    def open_now(self):
        self.is_open = True
        self.opened.emit(PortInfo("FAKE"))

    def write(self, data):
        self.written.append(bytes(data))
        self.data_sent.emit(bytes(data))
        return True

    def close(self, reason=""):
        self.is_open = False
        self.closed.emit(reason)


def fast(profile, **link):
    defaults = dict(reply_timeout_ms=30, retry_interval_ms=40, retries=2, lost_after_failures=3)
    defaults.update(link)
    return replace(profile, link=replace(profile.link, **defaults))


def decode(profile, data: bytes) -> Frame:
    (frame,) = FrameParser(profile.packet).feed(data)
    return frame


@pytest.fixture
def setup(qtbot, profile):
    def make(**link):
        prof = fast(profile, **link)
        transport = FakeTransport()
        link_manager = LinkManager(transport, prof)
        entries = []
        link_manager.log_entry.connect(entries.append)
        transport.open_now()
        return prof, transport, link_manager, entries

    return make


def reply(profile, sqn, ack=0, data=b""):
    return profile.packet.encode(sqn, bytes([ack]) + data)


def test_command_reply_roundtrip(qtbot, setup):
    profile, transport, link, entries = setup()
    link.send_command("SS_SA 10 -5")
    qtbot.waitUntil(lambda: len(transport.written) == 1)
    frame = decode(profile, transport.written[0])
    assert (frame.sqn, frame.payload) == (0, bytes([0x03, 10, 0xFB]))

    transport.data_received.emit(reply(profile, 0))
    assert link.state == LinkState.OK
    rx = [e for e in entries if e.direction == "rx"][-1]
    assert rx.status == "команда принята" and rx.rtt_ms is not None


def test_timeout_retries_with_new_sqn_then_link_lost(qtbot, setup):
    profile, transport, link, entries = setup()
    link.send_command("LED 0")
    qtbot.waitUntil(lambda: len(transport.written) == 3, timeout=2000)
    assert [decode(profile, w).sqn for w in transport.written] == [0, 1, 2]
    qtbot.waitUntil(lambda: link.state == LinkState.LOST, timeout=2000)
    assert link.stats.timeouts == 3


def test_late_reply_is_recognised(qtbot, setup):
    profile, transport, link, entries = setup(retries=0)
    link.send_command("GET_TEL")
    qtbot.waitUntil(lambda: link.stats.timeouts == 1)
    transport.data_received.emit(reply(profile, 0))
    assert entries[-1].status == "опоздал"


def test_drive_commands_coalesce(qtbot, setup):
    profile, transport, link, entries = setup()
    link.send_command("GET_TEL")  # in flight
    qtbot.waitUntil(lambda: len(transport.written) == 1)
    for speed in (10, 20, 30):
        link.send_command(f"SS_SA {speed} 0", "drive", coalesce="drive0", retries=0)
    transport.data_received.emit(reply(profile, 0))
    qtbot.waitUntil(lambda: len(transport.written) == 2)
    assert decode(profile, transport.written[1]).payload == bytes([0x03, 30, 0])


def test_echoed_packet_counts_as_alive(qtbot, setup):
    profile, transport, link, entries = setup()
    link.send_command("GET_TEL")
    qtbot.waitUntil(lambda: len(transport.written) == 1)
    transport.data_received.emit(transport.written[0])
    assert link.state == LinkState.OK
    assert entries[-1].status == "эхо"


def test_emergency_stop_jumps_the_queue(qtbot, setup):
    profile, transport, link, entries = setup()
    link.send_command("GET_TEL")
    qtbot.waitUntil(lambda: len(transport.written) == 1)
    link.send_command("LED 0")
    link.emergency_stop()
    transport.data_received.emit(reply(profile, 0))
    qtbot.waitUntil(lambda: len(transport.written) == 2)
    assert decode(profile, transport.written[1]).payload == bytes([0x03, 0, 0])
    transport.data_received.emit(reply(profile, 1))
    qtbot.wait(50)
    assert len(transport.written) == 2  # LED 0 was dropped


def test_fire_and_forget_mode(qtbot, setup):
    profile, transport, link, entries = setup(wait_for_reply=False)
    for i in range(3):
        link.send_command(f"SET_SPEED {i}")
    qtbot.waitUntil(lambda: len(transport.written) == 3)
    assert link.stats.timeouts == 0


def test_keepalive(qtbot, setup):
    profile, transport, link, entries = setup(keepalive_period_ms=50)
    link.set_keepalive_enabled(True)
    qtbot.waitUntil(lambda: len(transport.written) >= 1)
    assert decode(profile, transport.written[0]).payload == bytes([0x04])


def test_not_connected(qtbot, profile):
    link = LinkManager(FakeTransport(), profile)
    with pytest.raises(NotConnectedError):
        link.send_command("GET_TEL")


def test_keyboard_drive_ramps_and_stops(qtbot, setup):
    profile, transport, link, entries = setup(wait_for_reply=False)
    drive = DriveController(link, profile)
    drive.press("forward")
    qtbot.waitUntil(lambda: drive.output.values.speed == 127, timeout=2000)
    qtbot.wait(profile.drive.min_interval_ms + 20)  # the latest value goes out at the next send slot
    drive.release("forward")
    qtbot.waitUntil(lambda: drive.output.values.speed == 0, timeout=2000)
    qtbot.wait(60)
    speeds = [decode(profile, w).payload[1] for w in transport.written]
    assert speeds[-1] == 0 and 127 in speeds
    assert speeds == sorted(speeds[: speeds.index(127) + 1]) + speeds[speeds.index(127) + 1 :]


def test_ramp_does_not_flood_the_link(qtbot, setup):
    profile, transport, link, entries = setup(wait_for_reply=False)
    drive = DriveController(link, profile)
    drive.press("forward")
    qtbot.wait(500)  # a full ramp: a new value on every 20 ms tick
    drive.release("forward")
    qtbot.waitUntil(lambda: drive.output.values.speed == 0, timeout=2000)
    qtbot.wait(60)
    sent_at = [e.time for e in entries if e.direction == "tx"]
    assert len(sent_at) <= 1 + 1000 // profile.drive.min_interval_ms
    gaps_ms = [(b - a).total_seconds() * 1000 for a, b in zip(sent_at, sent_at[1:], strict=False)]
    # Only the final stop may jump the interval.
    assert all(gap >= profile.drive.min_interval_ms - 5 for gap in gaps_ms[:-1])
    assert decode(profile, transport.written[-1]).payload == bytes([0x03, 0, 0])


def test_gamepad_ramps_with_sensitivity(qtbot, setup):
    profile, transport, link, entries = setup(wait_for_reply=False)
    drive = DriveController(link, profile)
    drive.set_sensitivity(1.0, 0.5)  # steering at half the profile rate: 0.4 s to full lock
    drive.set_gamepad(0.0, 1.0)
    qtbot.wait(100)
    assert 0 < drive.output.values.angle < 127
    qtbot.waitUntil(lambda: drive.output.values.angle == 127, timeout=2000)
    drive.set_gamepad(0.0, 0.0)  # released: recentres through the ramp, not a jump
    qtbot.waitUntil(lambda: drive.output.values.angle == 0, timeout=2000)
    assert drive.output.source == "idle"


def test_emergency_latches_gamepad(qtbot, setup):
    profile, transport, link, entries = setup(wait_for_reply=False)
    drive = DriveController(link, profile)
    drive.set_gamepad(1.0, 0.0)
    qtbot.waitUntil(lambda: drive.output.values.speed == 127)
    drive.emergency_stop()
    qtbot.wait(60)
    assert drive.output.values.speed == 0  # stick still held, but ignored
    drive.set_gamepad(0.0, 0.0)
    drive.set_gamepad(0.5, 0.0)
    qtbot.waitUntil(lambda: drive.output.values.speed == 64)


def test_macro_runs_and_stops_the_car(qtbot, setup):
    profile, transport, link, entries = setup(wait_for_reply=False)
    drive = DriveController(link, profile)
    runner = MacroRunner(link, drive)
    steps = flatten(parse_macro("forward 0.1 speed=50 angle=-100\nsend LED 1"))
    with qtbot.waitSignal(runner.finished, timeout=2000) as done:
        runner.run(steps)
    assert done.args == [True, "выполнен"]
    qtbot.wait(60)
    payloads = [decode(profile, w).payload for w in transport.written]
    assert bytes([0x03, 64, (-127) & 0xFF]) in payloads
    assert bytes([0xFE, 1]) in payloads
    assert payloads[-1] == bytes([0x03, 0, 0])


def test_manual_input_aborts_macro(qtbot, setup):
    profile, transport, link, entries = setup(wait_for_reply=False)
    drive = DriveController(link, profile)
    runner = MacroRunner(link, drive)
    runner.run(flatten(parse_macro("forward 5")))
    with qtbot.waitSignal(runner.finished) as done:
        drive.press("left")
    assert done.args == [False, "прерван вручную"]


def test_loopback_port_end_to_end(qtbot, profile):
    transport = SerialTransport()
    link = LinkManager(transport, fast(profile, reply_timeout_ms=500))
    with qtbot.waitSignal(transport.opened, timeout=3000):
        transport.open("loop://", 19200)
    link.send_command("GET_TEL")
    qtbot.waitUntil(lambda: link.state == LinkState.OK, timeout=3000)

    tester = EchoTester(transport, link)
    with qtbot.waitSignal(tester.finished, timeout=5000) as done:
        tester.start(count=5, size=32, interval_ms=5, timeout_ms=500, text=False)
    stats, message = done.args
    assert (stats.ok, stats.lost, stats.mismatched, message) == (5, 0, 0, "завершён")
    assert not link.paused
    with qtbot.waitSignal(transport.closed):
        transport.close()
