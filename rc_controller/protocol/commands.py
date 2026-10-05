"""Text command lines <-> packet payloads.

One syntax is used everywhere: the terminal, drive templates in the profile,
and ``send`` steps in macros::

    SS_SA 50 -20        named command from the profile, arguments in order
    0x05 1 2            raw bytes: first one is CODE, the rest go out as-is

Numbers may be decimal, hex (0x..) or binary (0b..).
"""

from __future__ import annotations

from dataclasses import dataclass

from .framing import hex_bytes
from .profile import CommandSpec, FieldSpec, Profile, ProfileError


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class EncodedCommand:
    text: str
    name: str
    code: int
    args: tuple[int, ...]
    payload: bytes
    spec: CommandSpec | None


@dataclass(frozen=True)
class ReplyInfo:
    ack: int | None
    ack_text: str
    data: bytes
    fields: tuple[tuple[str, str], ...] = ()
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.ack == 0

    def summary(self) -> str:
        if self.ack is None:
            return "пустой ответ"
        parts = [f"ACK {self.ack}: {self.ack_text}"]
        if self.fields:
            parts.append(" ".join(f"{name}={value}" for name, value in self.fields))
        elif self.data:
            parts.append(f"DATA [{hex_bytes(self.data)}]")
        if self.note:
            parts.append(self.note)
        return " · ".join(parts)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def parse_int(token: str) -> int:
    try:
        return int(token, 0)
    except ValueError:
        pass
    # int(..., 0) rejects leading zeros like "007"; accept those as decimal.
    try:
        return int(token, 10)
    except ValueError:
        raise CommandError(f"«{token}» — не число (пишите 10, -5, 0x1F или 0b101)") from None


def encode_command(profile: Profile, text: str) -> EncodedCommand:
    tokens = text.split()
    if not tokens:
        raise CommandError("пустая команда")
    head = tokens[0]

    if head[0].isdigit() or head[0] in "+-":
        values = [parse_int(t) for t in tokens]
        for token, value in zip(tokens, values, strict=True):
            if not -128 <= value <= 255:
                raise CommandError(f"«{token}» не помещается в один байт (-128…255)")
        payload = bytes(v & 0xFF for v in values)
        spec = profile.by_code(payload[0])
        name = spec.name if spec else f"0x{payload[0]:02X}"
        encoded = EncodedCommand(
            text=" ".join(tokens),
            name=name,
            code=payload[0],
            args=tuple(values[1:]),
            payload=payload,
            spec=spec,
        )
    else:
        spec = profile.find(head)
        if spec is None:
            raise CommandError(f"неизвестная команда «{head}» (список команд — в справке, F1)")
        args = tokens[1:]
        if len(args) != len(spec.params):
            n = len(spec.params)
            word = _plural(n, "параметр", "параметра", "параметров")
            raise CommandError(f"{spec.name} ожидает {n} {word}: {spec.signature}")
        values = []
        chunks = [bytes([spec.code])]
        for param, token in zip(spec.params, args, strict=True):
            value = parse_int(token)
            if not param.min <= value <= param.max:
                raise CommandError(
                    f"{spec.name}: параметр {param.name} = {value} вне диапазона {param.min}…{param.max}"
                )
            values.append(value)
            chunks.append(value.to_bytes(param.size, profile.byte_order, signed=param.signed))
        payload = b"".join(chunks)
        encoded = EncodedCommand(
            text=" ".join([spec.name, *map(str, values)]),
            name=spec.name,
            code=spec.code,
            args=tuple(values),
            payload=payload,
            spec=spec,
        )

    if len(encoded.payload) > profile.packet.max_payload:
        raise CommandError(
            f"слишком длинная команда: {len(encoded.payload)} байт, максимум {profile.packet.max_payload}"
        )
    return encoded


def render_template(template: str, values: dict[str, int]) -> str:
    try:
        return template.format_map(values)
    except KeyError as exc:
        known = ", ".join("{" + k + "}" for k in values)
        raise CommandError(f"в шаблоне «{template}» неизвестная переменная {{{exc.args[0]}}}; есть: {known}") from None
    except (ValueError, IndexError) as exc:
        raise CommandError(f"ошибка в шаблоне «{template}»: {exc}") from None


DRIVE_VARIABLES = ("speed", "angle", "left", "right")


def check_profile_templates(profile: Profile) -> None:
    """Fail at load time if a drive/stop/keepalive template cannot be encoded."""
    drive = profile.drive
    checks: list[tuple[str, str, list[dict[str, int]]]] = []
    extremes = [
        {
            "speed": s * drive.max_speed,
            "angle": a * drive.max_angle,
            "left": s * drive.max_speed,
            "right": a * drive.max_speed,
        }
        for s in (-1, 0, 1)
        for a in (-1, 0, 1)
    ]
    for i, template in enumerate(drive.command):
        checks.append((f"drive.command[{i + 1}]", template, extremes))
    for key in ("stop", "brake"):
        for i, template in enumerate(getattr(drive, key)):
            checks.append((f"drive.{key}[{i + 1}]", template, [{}]))
    if profile.link.keepalive_command:
        checks.append(("link.keepalive_command", profile.link.keepalive_command, [{}]))

    for where, template, variants in checks:
        for values in variants:
            try:
                encode_command(profile, render_template(template, values))
            except CommandError as exc:
                detail = ", ".join(f"{k}={v}" for k, v in values.items())
                suffix = f" (при {detail})" if detail else ""
                raise ProfileError(f"{where}: {exc}{suffix}") from None


def _decode_int(data: bytes, offset: int, size: int, signed: bool, byte_order: str) -> int:
    return int.from_bytes(data[offset : offset + size], byte_order, signed=signed)  # type: ignore[arg-type]


def _format_field(spec: FieldSpec, raw: int) -> str:
    if spec.scale is None:
        return f"{raw}{spec.unit}"
    value = raw * spec.scale
    return f"{value:.4g}{spec.unit}"


def describe_payload(profile: Profile, payload: bytes) -> str:
    """Human-readable form of a command payload (for the log)."""
    if not payload:
        return "(пусто)"
    spec = profile.by_code(payload[0])
    if spec is None:
        rest = f" [{hex_bytes(payload[1:])}]" if len(payload) > 1 else ""
        return f"0x{payload[0]:02X}{rest}"
    expected = 1 + sum(p.size for p in spec.params)
    if len(payload) != expected:
        return f"{spec.name}? [{hex_bytes(payload[1:])}] (ожидалось {expected} байт, пришло {len(payload)})"
    parts = [spec.name]
    offset = 1
    for param in spec.params:
        value = _decode_int(payload, offset, param.size, param.signed, profile.byte_order)
        parts.append(f"{param.name}={value}")
        offset += param.size
    return " ".join(parts)


def decode_reply(profile: Profile, request: CommandSpec | None, payload: bytes) -> ReplyInfo:
    """Decode ACK + DATA, using the reply layout of the command it answers."""
    if not payload:
        return ReplyInfo(ack=None, ack_text="", data=b"")
    ack = payload[0]
    data = payload[1:]
    fields: list[tuple[str, str]] = []
    note = ""
    if request is not None and request.reply and data:
        offset = 0
        for spec in request.reply:
            if offset + spec.size > len(data):
                note = "данных меньше, чем описано в профиле"
                break
            raw = _decode_int(data, offset, spec.size, spec.signed, profile.byte_order)
            fields.append((spec.name, _format_field(spec, raw)))
            offset += spec.size
        if offset < len(data) and not note:
            note = f"ещё {len(data) - offset} байт: {hex_bytes(data[offset:])}"
    return ReplyInfo(ack=ack, ack_text=profile.ack_text(ack), data=data, fields=tuple(fields), note=note)
