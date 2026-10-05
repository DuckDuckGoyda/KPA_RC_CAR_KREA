"""Macro language: a plain-text list of driving steps.

Example::

    # Туда и обратно
    вперёд 1.5 скорость=80
    тормоз 0.5
    назад 1.5 скорость=60 угол=-30
    повтор 2
        команда LED 0
        пауза 0.3
        команда LED 1
    конец

English keywords work too (forward, backward, stop, brake, wait, send,
text, hex, speed, repeat, end). Speed and angle are percentages of the
profile's maximum; the speed-limit slider scales them further.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_SPEED = 50.0  # %
DEFAULT_BRAKE_S = 0.5
MAX_REPEAT = 1000
MAX_FLAT_STEPS = 100_000

_KEYWORDS: dict[str, str] = {}
for _canonical, _aliases in {
    "forward": ("forward", "вперед", "вперёд"),
    "backward": ("backward", "back", "назад"),
    "stop": ("stop", "стоп"),
    "brake": ("brake", "тормоз"),
    "wait": ("wait", "пауза", "ждать"),
    "send": ("send", "команда", "отправить"),
    "text": ("text", "текст"),
    "hex": ("hex",),
    "speed": ("speed", "скорость"),
    "repeat": ("repeat", "повтор", "повторить"),
    "end": ("end", "конец"),
}.items():
    for _alias in _aliases:
        _KEYWORDS[_alias] = _canonical

_OPTIONS = {"speed": "speed", "скорость": "speed", "angle": "angle", "угол": "angle"}

_DURATION = re.compile(r"^(\d+(?:[.,]\d+)?)(s|с|сек|ms|мс)?$", re.IGNORECASE)


class MacroError(ValueError):
    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"строка {line}: {message}")
        self.line = line
        self.message = message


@dataclass(frozen=True)
class DriveStep:
    line: int
    action: str  # forward | backward | stop | brake
    duration: float
    speed: float = 0.0  # 0..1 of max speed
    angle: float = 0.0  # -1..1 of max angle

    def describe(self) -> str:
        names = {"forward": "вперёд", "backward": "назад", "stop": "стоп", "brake": "тормоз"}
        text = f"{names[self.action]} {self.duration:g} с"
        if self.action in ("forward", "backward"):
            text += f", скорость {self.speed * 100:g} %"
            if self.angle:
                text += f", угол {self.angle * 100:g} %"
        return text


@dataclass(frozen=True)
class WaitStep:
    line: int
    duration: float

    def describe(self) -> str:
        return f"пауза {self.duration:g} с"


@dataclass(frozen=True)
class SendStep:
    line: int
    command: str

    def describe(self) -> str:
        return f"команда {self.command}"


@dataclass(frozen=True)
class RawStep:
    line: int
    data: bytes
    display: str

    def describe(self) -> str:
        return f"данные {self.display}"


@dataclass(frozen=True)
class RepeatBlock:
    line: int
    count: int
    body: tuple[Step, ...]


Step = DriveStep | WaitStep | SendStep | RawStep | RepeatBlock
FlatStep = DriveStep | WaitStep | SendStep | RawStep


def parse_duration(token: str, line: int) -> float:
    match = _DURATION.match(token.strip())
    if not match:
        raise MacroError(line, f"«{token}» — не длительность (пример: 1.5, 1.5с, 500мс)")
    value = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or "s").lower()
    return value / 1000 if unit in ("ms", "мс") else value


def _parse_percent(token: str, line: int, name: str, lo: float, hi: float) -> float:
    text = token.strip().rstrip("%").replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        raise MacroError(line, f"{name}: «{token}» — не число") from None
    if not lo <= value <= hi:
        raise MacroError(line, f"{name}: допустимо от {lo:g} до {hi:g} %")
    return value


def decode_escapes(text: str) -> bytes:
    """Turn ``AT\\r\\n`` style escapes into bytes; other characters are UTF-8."""
    out = bytearray()
    i = 0
    simple = {"r": 13, "n": 10, "t": 9, "0": 0, "\\": 92, '"': 34, "'": 39}
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if nxt == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", text[i + 2 : i + 4]):
                out.append(int(text[i + 2 : i + 4], 16))
                i += 4
                continue
        out += ch.encode("utf-8")
        i += 1
    return bytes(out)


def _tokenize(line_text: str, line: int) -> list[str]:
    lexer = shlex.shlex(line_text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    lexer.escape = ""  # keep backslashes for decode_escapes
    try:
        return list(lexer)
    except ValueError as exc:
        raise MacroError(line, f"не закрыта кавычка ({exc})") from None


def parse_macro(text: str, validate_command: Callable[[str], object] | None = None) -> tuple[Step, ...]:
    """Parse macro text. ``validate_command`` raises on a bad ``send`` line."""
    root: list[Step] = []
    stack: list[tuple[int, int, list[Step]]] = []  # (line, count, parent body)
    body = root
    default_speed = DEFAULT_SPEED

    for number, raw_line in enumerate(text.splitlines(), start=1):
        tokens = _tokenize(raw_line, number)
        if not tokens:
            continue
        head = tokens[0].lower()
        keyword = _KEYWORDS.get(head)
        args = tokens[1:]
        if keyword is None:
            raise MacroError(number, f"неизвестное действие «{tokens[0]}»")

        if keyword in ("forward", "backward"):
            if not args:
                raise MacroError(number, f"{tokens[0]}: укажите длительность, например «{tokens[0]} 1.5»")
            duration = parse_duration(args[0], number)
            speed, angle = default_speed, 0.0
            for option in args[1:]:
                key, _, value = option.partition("=")
                name = _OPTIONS.get(key.lower())
                if not value or name is None:
                    raise MacroError(number, f"«{option}» — ожидалось скорость=… или угол=…")
                if name == "speed":
                    speed = _parse_percent(value, number, "скорость", 0, 100)
                else:
                    angle = _parse_percent(value, number, "угол", -100, 100)
            body.append(DriveStep(number, keyword, duration, speed / 100, angle / 100))

        elif keyword in ("stop", "brake"):
            if len(args) > 1:
                raise MacroError(number, f"{tokens[0]}: лишние параметры")
            default = DEFAULT_BRAKE_S if keyword == "brake" else 0.0
            duration = parse_duration(args[0], number) if args else default
            body.append(DriveStep(number, keyword, duration))

        elif keyword == "wait":
            if len(args) != 1:
                raise MacroError(number, f"{tokens[0]}: укажите одну длительность")
            body.append(WaitStep(number, parse_duration(args[0], number)))

        elif keyword == "speed":
            if len(args) != 1:
                raise MacroError(number, f"{tokens[0]}: укажите скорость в процентах")
            default_speed = _parse_percent(args[0], number, "скорость", 0, 100)

        elif keyword == "send":
            if not args:
                raise MacroError(number, f"{tokens[0]}: укажите команду, например «{tokens[0]} LED 0»")
            command = " ".join(args)
            if validate_command is not None:
                try:
                    validate_command(command)
                except ValueError as exc:
                    raise MacroError(number, str(exc)) from None
            body.append(SendStep(number, command))

        elif keyword == "text":
            if len(args) != 1:
                raise MacroError(number, 'текст: возьмите строку в кавычки, например текст "AT\\r\\n"')
            body.append(RawStep(number, decode_escapes(args[0]), f'"{args[0]}"'))

        elif keyword == "hex":
            try:
                data = bytes.fromhex("".join(args))
            except ValueError:
                raise MacroError(number, "hex: ожидались байты вида AC 53 04") from None
            if not data:
                raise MacroError(number, "hex: нет данных")
            body.append(RawStep(number, data, data.hex(" ").upper()))

        elif keyword == "repeat":
            if len(args) != 1 or not args[0].isdigit():
                raise MacroError(number, f"{tokens[0]}: укажите число повторов, например «{tokens[0]} 3»")
            count = int(args[0])
            if not 1 <= count <= MAX_REPEAT:
                raise MacroError(number, f"число повторов должно быть от 1 до {MAX_REPEAT}")
            stack.append((number, count, body))
            body = []

        elif keyword == "end":
            if args:
                raise MacroError(number, f"{tokens[0]}: лишние параметры")
            if not stack:
                raise MacroError(number, f"«{tokens[0]}» без «повтор»")
            start, count, parent = stack.pop()
            parent.append(RepeatBlock(start, count, tuple(body)))
            body = parent

    if stack:
        raise MacroError(stack[-1][0], "«повтор» не закрыт словом «конец»")
    return tuple(root)


def flatten(steps: tuple[Step, ...] | list[Step]) -> list[FlatStep]:
    out: list[FlatStep] = []

    def walk(items: tuple[Step, ...] | list[Step]) -> None:
        for step in items:
            if isinstance(step, RepeatBlock):
                for _ in range(step.count):
                    walk(step.body)
            else:
                out.append(step)
                if len(out) > MAX_FLAT_STEPS:
                    raise MacroError(step.line, f"макрос слишком длинный (больше {MAX_FLAT_STEPS} шагов)")

    walk(steps)
    return out


def total_duration(steps: list[FlatStep]) -> float:
    return sum(s.duration for s in steps if isinstance(s, (DriveStep, WaitStep)))
