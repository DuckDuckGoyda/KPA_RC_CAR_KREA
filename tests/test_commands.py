import pytest

from rc_controller.protocol.commands import (
    CommandError,
    decode_reply,
    describe_payload,
    encode_command,
    render_template,
)


def test_named_command(profile):
    cmd = encode_command(profile, "ss_sa 50 -20")
    assert cmd.name == "SS_SA"
    assert cmd.text == "SS_SA 50 -20"
    assert cmd.payload == bytes([0x03, 50, 0xEC])


def test_hex_and_binary_arguments(profile):
    assert encode_command(profile, "SET_SPEED 0x10").payload == bytes([0x01, 0x10])
    assert encode_command(profile, "SET_SPEED 0b11").payload == bytes([0x01, 3])


def test_raw_bytes(profile):
    cmd = encode_command(profile, "0x05 1 -1 255")
    assert cmd.payload == bytes([0x05, 0x01, 0xFF, 0xFF])
    assert cmd.name == "0x05"
    assert encode_command(profile, "4").name == "GET_TEL"


def test_errors_are_readable(profile):
    with pytest.raises(CommandError, match="неизвестная команда"):
        encode_command(profile, "FLY 1")
    with pytest.raises(CommandError, match="ожидает 2 параметра"):
        encode_command(profile, "SS_SA 1")
    with pytest.raises(CommandError, match="вне диапазона -127…127"):
        encode_command(profile, "SET_SPEED -128")
    with pytest.raises(CommandError, match="не число"):
        encode_command(profile, "SET_SPEED fast")
    with pytest.raises(CommandError, match="один байт"):
        encode_command(profile, "0x05 300")


def test_template_rendering(profile):
    assert render_template("SS_SA {speed} {angle}", {"speed": 5, "angle": -3}) == "SS_SA 5 -3"
    with pytest.raises(CommandError, match="неизвестная переменная"):
        render_template("SS_SA {sped}", {"speed": 1})


def test_describe_payload(profile):
    assert describe_payload(profile, bytes([0x03, 50, 0xEC])) == "SS_SA speed=50 angle=-20"
    assert describe_payload(profile, bytes([0x42, 1])) == "0x42 [01]"


def test_decode_telemetry_reply(profile):
    data = bytes([5, 0x10, 0x00, 0xFF, 0xFF, 0, 0, 0, 0, 0, 0, 0x20, 0x03, 200, 0xFE])
    info = decode_reply(profile, profile.find("GET_TEL"), bytes([0]) + data)
    assert info.ok
    fields = dict(info.fields)
    assert fields["TLM_NUM"] == "5"
    assert fields["SPEED"] == "16"
    assert fields["PITCH"] == "-1"
    assert fields["DIST"] == "800"
    assert fields["VALID"] == "254"
    assert info.summary().startswith("ACK 0: команда принята")


def test_decode_short_and_nack_replies(profile):
    info = decode_reply(profile, profile.find("GET_TEL"), bytes([0, 5]))
    assert "меньше" in info.note
    nack = decode_reply(profile, profile.find("SS_SA"), bytes([3]))
    assert not nack.ok and nack.ack_text == "недопустимый параметр"
