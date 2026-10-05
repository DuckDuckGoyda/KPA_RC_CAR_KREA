import pytest

from rc_controller.protocol.crc import DEFAULT_CRC, PRESETS, Crc8


@pytest.mark.parametrize(
    ("name", "check"),
    [
        ("CRC-8/MAXIM-DOW", 0xA1),
        ("CRC-8/0x31", 0xA2),
        ("CRC-8/NRSC-5", 0xF7),
        ("CRC-8/SMBUS", 0xF4),
    ],
)
def test_catalogue_check_values(name, check):
    assert PRESETS[name].check == check


def test_default_is_maxim():
    assert DEFAULT_CRC.name == "CRC-8/MAXIM-DOW"


def test_bitwise_reference_matches_table():
    """Table-driven result must equal a plain bit-by-bit reflected CRC (what firmware usually does)."""

    def maxim_bitwise(data: bytes) -> int:
        crc = 0
        for byte in data:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
        return crc

    for data in (b"", b"\x00", b"\x04\x00\x01\x04", bytes(range(256))):
        assert DEFAULT_CRC(data) == maxim_bitwise(data)


def test_rejects_out_of_range_parameters():
    with pytest.raises(ValueError):
        Crc8("bad", poly=0x131)
