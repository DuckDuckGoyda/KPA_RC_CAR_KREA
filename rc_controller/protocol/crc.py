"""Parametrised CRC-8 (Rocksoft/Williams model).

The protocol spec only fixes the polynomial (x^8 + x^5 + x^4 + 1 = 0x31).
The project settled on CRC-8/MAXIM-DOW; other variants stay selectable
through the profile in case the firmware side ends up doing something else.
"""

from __future__ import annotations

from dataclasses import dataclass


def _reflect8(value: int) -> int:
    result = 0
    for _ in range(8):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


@dataclass(frozen=True)
class Crc8:
    name: str
    poly: int
    init: int = 0x00
    refin: bool = False
    refout: bool = False
    xorout: int = 0x00

    def __post_init__(self) -> None:
        for field_name in ("poly", "init", "xorout"):
            value = getattr(self, field_name)
            if not 0 <= value <= 0xFF:
                raise ValueError(f"CRC {field_name} must fit in one byte, got {value:#x}")
        # A 256-entry lookup table keeps per-byte cost to one index + xor.
        object.__setattr__(self, "_table", self._build_table())

    def _build_table(self) -> tuple[int, ...]:
        table = []
        for byte in range(256):
            crc = byte
            for _ in range(8):
                crc = ((crc << 1) ^ self.poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
            table.append(crc)
        return tuple(table)

    def compute(self, data: bytes | bytearray | memoryview) -> int:
        table = self._table  # type: ignore[attr-defined]
        crc = self.init
        if self.refin:
            for byte in data:
                crc = table[crc ^ _reflect8(byte)]
        else:
            for byte in data:
                crc = table[crc ^ byte]
        if self.refout:
            crc = _reflect8(crc)
        return crc ^ self.xorout

    __call__ = compute

    @property
    def check(self) -> int:
        """CRC of b"123456789", the standard catalogue check value."""
        return self.compute(b"123456789")


PRESETS: dict[str, Crc8] = {
    preset.name: preset
    for preset in (
        # Dallas/Maxim 1-Wire CRC. check = 0xA1. This is the project default.
        Crc8("CRC-8/MAXIM-DOW", poly=0x31, init=0x00, refin=True, refout=True),
        # Same polynomial, no reflection, zero init. check = 0xA2.
        Crc8("CRC-8/0x31", poly=0x31),
        # Same polynomial, init 0xFF (also used by Sensirion sensors). check = 0xF7.
        Crc8("CRC-8/NRSC-5", poly=0x31, init=0xFF),
        # The most common CRC-8 overall (poly 0x07), handy if the firmware uses a HAL default.
        Crc8("CRC-8/SMBUS", poly=0x07),
    )
}

DEFAULT_CRC = PRESETS["CRC-8/MAXIM-DOW"]
