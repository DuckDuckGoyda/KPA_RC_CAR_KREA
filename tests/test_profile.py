import tomllib

import pytest

from rc_controller.protocol.profile import ProfileError, parse_profile

from .conftest import DEFAULT_PROFILE


def default_data() -> dict:
    return tomllib.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))


def test_default_profile_loads(profile):
    assert profile.find("ss_sa").code == 0x03
    assert profile.by_code(0x04).name == "GET_TEL"
    assert profile.packet.sync == b"\xac\x53"
    assert profile.packet.crc.name == "CRC-8/MAXIM-DOW"
    assert profile.link.baudrate == 19200
    assert profile.ack_text(2) == "ошибка CRC"


def test_minimal_profile_uses_defaults():
    profile = parse_profile({"command": [{"name": "PING", "code": 1}]})
    assert profile.link.baudrate == 19200
    assert profile.drive.command == ()


def test_unknown_key_is_reported():
    data = default_data()
    data["link"]["keepalive_comand"] = "GET_TEL"
    with pytest.raises(ProfileError, match="keepalive_comand"):
        parse_profile(data)


def test_bad_param_type_is_reported():
    data = default_data()
    data["command"][0]["params"][0]["type"] = "int9"
    with pytest.raises(ProfileError, match=r"command\[1\]\.params\[1\]\.type"):
        parse_profile(data)


def test_duplicate_codes_rejected():
    data = default_data()
    data["command"][1]["code"] = data["command"][0]["code"]
    with pytest.raises(ProfileError, match="код 0x01"):
        parse_profile(data)


def test_drive_template_checked_against_ranges():
    data = default_data()
    data["drive"]["max_speed"] = 200  # SS_SA speed is int8 -127..127
    with pytest.raises(ProfileError, match=r"drive\.command"):
        parse_profile(data)


def test_drive_template_unknown_command():
    data = default_data()
    data["drive"]["stop"] = ["HALT"]
    with pytest.raises(ProfileError, match="HALT"):
        parse_profile(data)


def test_custom_crc_table():
    data = default_data()
    data["packet"]["crc"] = {"poly": 0x07}
    profile = parse_profile(data)
    assert profile.packet.crc.check == 0xF4


def test_brake_defaults_to_stop():
    data = default_data()
    del data["drive"]["brake"]
    assert parse_profile(data).drive.brake == ("SS_SA 0 0",)
