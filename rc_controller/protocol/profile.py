"""Protocol profile: everything car-specific, loaded from one TOML file.

The code never hard-codes command names or codes; it only reads them from
here, so the command set can change with the firmware.
Validation errors are in Russian because teammates edit these files.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .crc import PRESETS, Crc8
from .framing import PacketFormat

PARAM_TYPES: dict[str, tuple[int, bool]] = {
    "int8": (1, True),
    "uint8": (1, False),
    "int16": (2, True),
    "uint16": (2, False),
    "int32": (4, True),
    "uint32": (4, False),
}

DRIVE_MODES = ("speed_angle", "differential")
GAMEPAD_STICKS = ("left_stick", "right_stick")
GAMEPAD_THROTTLES = ("auto", "triggers", *GAMEPAD_STICKS)
GAMEPAD_BUTTONS = ("A", "B", "X", "Y", "LB", "RB", "BACK", "START")


class ProfileError(ValueError):
    pass


def type_range(type_name: str) -> tuple[int, int]:
    size, signed = PARAM_TYPES[type_name]
    bits = size * 8
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str
    min: int
    max: int
    description: str = ""

    @property
    def size(self) -> int:
        return PARAM_TYPES[self.type][0]

    @property
    def signed(self) -> bool:
        return PARAM_TYPES[self.type][1]


@dataclass(frozen=True)
class FieldSpec:
    """One field of a reply's DATA part."""

    name: str
    type: str
    description: str = ""
    scale: float | None = None
    unit: str = ""

    @property
    def size(self) -> int:
        return PARAM_TYPES[self.type][0]

    @property
    def signed(self) -> bool:
        return PARAM_TYPES[self.type][1]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    code: int
    params: tuple[ParamSpec, ...] = ()
    description: str = ""
    reply: tuple[FieldSpec, ...] = ()

    @property
    def signature(self) -> str:
        return " ".join([self.name] + [f"<{p.name}>" for p in self.params])


@dataclass(frozen=True)
class LinkSettings:
    baudrate: int = 19200
    wait_for_reply: bool = True
    reply_timeout_ms: int = 200
    retries: int = 3
    retry_interval_ms: int = 1000
    lost_after_failures: int = 3
    keepalive_command: str = ""
    keepalive_period_ms: int = 1000
    keepalive_enabled: bool = True
    frame_timeout_ms: int = 100


@dataclass(frozen=True)
class DriveSettings:
    mode: str = "speed_angle"
    command: tuple[str, ...] = ()
    stop: tuple[str, ...] = ()
    brake: tuple[str, ...] = ()
    max_speed: int = 127
    max_angle: int = 127
    invert_speed: bool = False
    invert_angle: bool = False
    resend_ms: int = 200
    min_interval_ms: int = 100
    accel: float = 2.0
    decel: float = 3.0
    steer_rate: float = 5.0
    center_rate: float = 8.0


@dataclass(frozen=True)
class KeySettings:
    forward: str = "W"
    backward: str = "S"
    left: str = "A"
    right: str = "D"
    stop: str = "Space"

    def as_dict(self) -> dict[str, str]:
        return {
            "forward": self.forward,
            "backward": self.backward,
            "left": self.left,
            "right": self.right,
            "stop": self.stop,
        }


@dataclass(frozen=True)
class GamepadSettings:
    enabled: bool = True
    throttle: str = "auto"
    steering: str = "left_stick"
    deadzone: float = 0.15
    stop_button: str = "B"


@dataclass(frozen=True)
class Profile:
    name: str
    description: str = ""
    source: str = ""
    packet: PacketFormat = field(default_factory=PacketFormat)
    byte_order: str = "little"
    acks: dict[int, str] = field(default_factory=dict)
    commands: tuple[CommandSpec, ...] = ()
    link: LinkSettings = field(default_factory=LinkSettings)
    drive: DriveSettings = field(default_factory=DriveSettings)
    keys: KeySettings = field(default_factory=KeySettings)
    gamepad: GamepadSettings = field(default_factory=GamepadSettings)

    def find(self, name: str) -> CommandSpec | None:
        wanted = name.upper()
        for spec in self.commands:
            if spec.name.upper() == wanted:
                return spec
        return None

    def by_code(self, code: int) -> CommandSpec | None:
        for spec in self.commands:
            if spec.code == code:
                return spec
        return None

    def ack_text(self, ack: int) -> str:
        return self.acks.get(ack, "неизвестный код")


# --------------------------------------------------------------------------- loading

_REQUIRED = object()


class _Section:
    """Typed access to one TOML table, with Russian error messages and typo detection."""

    def __init__(self, data: Any, where: str) -> None:
        if not isinstance(data, dict):
            raise ProfileError(f"{where}: ожидалась таблица (секция), получено {_type_ru(data)}")
        self.data = data
        self.where = where
        self.used: set[str] = set()

    def _path(self, key: str) -> str:
        return f"{self.where}.{key}" if self.where else key

    def raw(self, key: str, default: Any = _REQUIRED) -> Any:
        self.used.add(key)
        if key not in self.data:
            if default is _REQUIRED:
                raise ProfileError(f"{self._path(key)}: обязательный параметр не задан")
            return default
        return self.data[key]

    def int(self, key: str, default: Any = _REQUIRED, lo: int | None = None, hi: int | None = None) -> int:
        value = self.raw(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProfileError(f"{self._path(key)}: ожидалось целое число, получено {_type_ru(value)}")
        if lo is not None and value < lo or hi is not None and value > hi:
            raise ProfileError(f"{self._path(key)}: значение {value} вне диапазона {lo}…{hi}")
        return value

    def float(self, key: str, default: Any = _REQUIRED, lo: float | None = None) -> float:
        value = self.raw(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProfileError(f"{self._path(key)}: ожидалось число, получено {_type_ru(value)}")
        if lo is not None and value < lo:
            raise ProfileError(f"{self._path(key)}: значение должно быть не меньше {lo}")
        return float(value)

    def bool(self, key: str, default: Any = _REQUIRED) -> bool:
        value = self.raw(key, default)
        if not isinstance(value, bool):
            raise ProfileError(f"{self._path(key)}: ожидалось true или false, получено {_type_ru(value)}")
        return value

    def str(self, key: str, default: Any = _REQUIRED, choices: tuple[str, ...] | None = None) -> str:
        value = self.raw(key, default)
        if not isinstance(value, str):
            raise ProfileError(f"{self._path(key)}: ожидалась строка в кавычках, получено {_type_ru(value)}")
        if choices is not None and value not in choices:
            raise ProfileError(f"{self._path(key)}: недопустимое значение «{value}», допустимо: {', '.join(choices)}")
        return value

    def str_list(self, key: str, default: Any = _REQUIRED) -> tuple[str, ...]:
        value = self.raw(key, default)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
            raise ProfileError(f"{self._path(key)}: ожидалась строка или список строк")
        return tuple(value)

    def section(self, key: str) -> _Section:
        return _Section(self.raw(key, {}), self._path(key))

    def table_list(self, key: str) -> list[_Section]:
        value = self.raw(key, [])
        if not isinstance(value, list):
            raise ProfileError(f"{self._path(key)}: ожидался список таблиц")
        return [_Section(item, f"{self._path(key)}[{i + 1}]") for i, item in enumerate(value)]

    def finish(self) -> None:
        unknown = sorted(set(self.data) - self.used)
        if unknown:
            names = ", ".join(unknown)
            raise ProfileError(f"{self.where or 'корень файла'}: неизвестные параметры: {names} (опечатка?)")


def _type_ru(value: Any) -> str:
    if isinstance(value, bool):
        return "логическое значение"
    if isinstance(value, int):
        return f"целое число {value}"
    if isinstance(value, float):
        return f"дробное число {value}"
    if isinstance(value, str):
        return f"строка «{value}»"
    if isinstance(value, list):
        return "список"
    if isinstance(value, dict):
        return "таблица"
    return type(value).__name__


def _parse_crc(sec: _Section) -> Crc8:
    value = sec.raw("crc", "CRC-8/MAXIM-DOW")
    if isinstance(value, str):
        if value not in PRESETS:
            raise ProfileError(
                f"packet.crc: неизвестный вариант «{value}», допустимо: {', '.join(PRESETS)} "
                "или таблица {{ poly = …, init = …, refin = …, refout = …, xorout = … }}"
            )
        return PRESETS[value]
    crc = _Section(value, "packet.crc")
    result = Crc8(
        name="custom",
        poly=crc.int("poly", lo=0, hi=0xFF),
        init=crc.int("init", 0, lo=0, hi=0xFF),
        refin=crc.bool("refin", False),
        refout=crc.bool("refout", False),
        xorout=crc.int("xorout", 0, lo=0, hi=0xFF),
    )
    crc.finish()
    return result


def _parse_param(sec: _Section) -> ParamSpec:
    name = sec.str("name")
    type_name = sec.str("type", choices=tuple(PARAM_TYPES))
    lo, hi = type_range(type_name)
    p_min = sec.int("min", lo, lo=lo, hi=hi)
    p_max = sec.int("max", hi, lo=lo, hi=hi)
    if p_min > p_max:
        raise ProfileError(f"{sec.where}: min больше max")
    spec = ParamSpec(name, type_name, p_min, p_max, sec.str("description", ""))
    sec.finish()
    return spec


def _parse_field(sec: _Section) -> FieldSpec:
    scale = sec.raw("scale", None)
    if scale is not None and (isinstance(scale, bool) or not isinstance(scale, (int, float))):
        raise ProfileError(f"{sec.where}.scale: ожидалось число")
    spec = FieldSpec(
        name=sec.str("name"),
        type=sec.str("type", choices=tuple(PARAM_TYPES)),
        description=sec.str("description", ""),
        scale=float(scale) if scale is not None else None,
        unit=sec.str("unit", ""),
    )
    sec.finish()
    return spec


def _parse_command(sec: _Section) -> CommandSpec:
    name = sec.str("name")
    if not name or not name.replace("_", "").isalnum() or name[0].isdigit():
        raise ProfileError(f"{sec.where}.name: «{name}» — имя должно состоять из букв, цифр и _")
    spec = CommandSpec(
        name=name,
        code=sec.int("code", lo=0, hi=0xFF),
        params=tuple(_parse_param(p) for p in sec.table_list("params")),
        description=sec.str("description", ""),
        reply=tuple(_parse_field(f) for f in sec.table_list("reply")),
    )
    sec.finish()
    return spec


def parse_profile(data: dict[str, Any], source: str = "") -> Profile:
    root = _Section(data, "")

    meta = root.section("profile")
    name = meta.str("name", Path(source).stem if source else "без имени")
    description = meta.str("description", "")
    meta.finish()

    pk = root.section("packet")
    sync_raw = pk.raw("sync", [0xAC, 0x53])
    if (
        not isinstance(sync_raw, list)
        or not sync_raw
        or not all(isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= 0xFF for b in sync_raw)
    ):
        raise ProfileError("packet.sync: ожидался непустой список байт, например [0xAC, 0x53]")
    packet = PacketFormat(
        sync=bytes(sync_raw),
        address=pk.int("address", 1, lo=0, hi=0xFF),
        crc=_parse_crc(pk),
        sqn_modulo=pk.int("sqn_modulo", 255, lo=1, hi=256),
    )
    byte_order = pk.str("byte_order", "little", choices=("little", "big"))
    pk.finish()

    ack_sec = root.section("acks")
    acks: dict[int, str] = {}
    for key in list(ack_sec.data):
        try:
            code = int(key, 0)
        except ValueError:
            raise ProfileError(f"acks: ключ «{key}» должен быть числом (0, 1, 0x02…)") from None
        acks[code] = ack_sec.str(key)
    ack_sec.finish()

    commands = tuple(_parse_command(c) for c in root.table_list("command"))
    seen_names: set[str] = set()
    seen_codes: dict[int, str] = {}
    for spec in commands:
        if spec.name.upper() in seen_names:
            raise ProfileError(f"command: имя {spec.name} встречается дважды")
        seen_names.add(spec.name.upper())
        if spec.code in seen_codes:
            raise ProfileError(f"command: код 0x{spec.code:02X} занят и {seen_codes[spec.code]}, и {spec.name}")
        seen_codes[spec.code] = spec.name

    ln = root.section("link")
    link = LinkSettings(
        baudrate=ln.int("baudrate", 19200, lo=300, hi=4_000_000),
        wait_for_reply=ln.bool("wait_for_reply", True),
        reply_timeout_ms=ln.int("reply_timeout_ms", 200, lo=1, hi=60_000),
        retries=ln.int("retries", 3, lo=0, hi=100),
        retry_interval_ms=ln.int("retry_interval_ms", 1000, lo=0, hi=60_000),
        lost_after_failures=ln.int("lost_after_failures", 3, lo=1, hi=1000),
        keepalive_command=ln.str("keepalive_command", ""),
        keepalive_period_ms=ln.int("keepalive_period_ms", 1000, lo=50, hi=600_000),
        keepalive_enabled=ln.bool("keepalive_enabled", True),
        frame_timeout_ms=ln.int("frame_timeout_ms", 100, lo=1, hi=10_000),
    )
    ln.finish()

    dr = root.section("drive")
    drive = DriveSettings(
        mode=dr.str("mode", "speed_angle", choices=DRIVE_MODES),
        command=dr.str_list("command", ()),
        stop=dr.str_list("stop", ()),
        brake=dr.str_list("brake", ()),
        max_speed=dr.int("max_speed", 127, lo=1, hi=1 << 31),
        max_angle=dr.int("max_angle", 127, lo=1, hi=1 << 31),
        invert_speed=dr.bool("invert_speed", False),
        invert_angle=dr.bool("invert_angle", False),
        resend_ms=dr.int("resend_ms", 200, lo=20, hi=60_000),
        min_interval_ms=dr.int("min_interval_ms", 100, lo=0, hi=60_000),
        accel=dr.float("accel", 2.0, lo=0.01),
        decel=dr.float("decel", 3.0, lo=0.01),
        steer_rate=dr.float("steer_rate", 5.0, lo=0.01),
        center_rate=dr.float("center_rate", 8.0, lo=0.01),
    )
    dr.finish()
    if not drive.brake and drive.stop:
        drive = replace(drive, brake=drive.stop)

    ks = root.section("keys")
    keys = KeySettings(
        forward=ks.str("forward", "W"),
        backward=ks.str("backward", "S"),
        left=ks.str("left", "A"),
        right=ks.str("right", "D"),
        stop=ks.str("stop", "Space"),
    )
    ks.finish()

    gp = root.section("gamepad")
    gamepad = GamepadSettings(
        enabled=gp.bool("enabled", True),
        throttle=gp.str("throttle", "auto", choices=GAMEPAD_THROTTLES),
        steering=gp.str("steering", "left_stick", choices=GAMEPAD_STICKS),
        deadzone=gp.float("deadzone", 0.15, lo=0.0),
        stop_button=gp.str("stop_button", "B", choices=GAMEPAD_BUTTONS),
    )
    gp.finish()
    if gamepad.deadzone >= 1.0:
        raise ProfileError("gamepad.deadzone: должно быть меньше 1.0")

    root.finish()

    profile = Profile(
        name=name,
        description=description,
        source=source,
        packet=packet,
        byte_order=byte_order,
        acks=acks,
        commands=commands,
        link=link,
        drive=drive,
        keys=keys,
        gamepad=gamepad,
    )

    # Templates reference commands by name; check them now rather than mid-drive.
    from .commands import check_profile_templates

    check_profile_templates(profile)
    return profile


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ProfileError(f"не удалось прочитать файл {path}: {exc}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"ошибка синтаксиса TOML: {exc}") from exc
    return parse_profile(data, source=str(path))
