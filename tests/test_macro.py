import pytest

from rc_controller.control.macro import (
    DriveStep,
    MacroError,
    RawStep,
    RepeatBlock,
    SendStep,
    WaitStep,
    decode_escapes,
    flatten,
    parse_duration,
    parse_macro,
    total_duration,
)
from rc_controller.protocol.commands import encode_command

from .conftest import DEFAULT_PROFILE

EXAMPLE = """
# Туда и обратно
вперёд 1.5 скорость=80
тормоз
назад 1,5с скорость=60 угол=-30
пауза 500мс
повтор 2
    команда LED 0   # мигнуть
    wait 0.2
    send LED 1
конец
текст "AT\\r\\n"
hex AC 53 04
"""


def test_example_parses(profile):
    steps = parse_macro(EXAMPLE, lambda text: encode_command(profile, text))
    assert steps[0] == DriveStep(3, "forward", 1.5, 0.8, 0.0)
    assert steps[1] == DriveStep(4, "brake", 0.5)
    assert steps[2] == DriveStep(5, "backward", 1.5, 0.6, -0.3)
    assert steps[3] == WaitStep(6, 0.5)
    block = steps[4]
    assert isinstance(block, RepeatBlock) and block.count == 2 and len(block.body) == 3
    assert block.body[0] == SendStep(8, "LED 0")
    assert steps[5] == RawStep(12, b"AT\r\n", '"AT\\r\\n"')
    assert steps[6].data == b"\xac\x53\x04"

    flat = flatten(steps)
    assert len(flat) == 4 + 6 + 2
    assert total_duration(flat) == pytest.approx(1.5 + 0.5 + 1.5 + 0.5 + 0.4)


def test_default_speed_directive():
    steps = parse_macro("скорость 30\nforward 1\nforward 1 speed=90")
    assert steps[0].speed == pytest.approx(0.3)
    assert steps[1].speed == pytest.approx(0.9)


@pytest.mark.parametrize(
    ("text", "line", "fragment"),
    [
        ("fly 1", 1, "неизвестное действие"),
        ("forward", 1, "укажите длительность"),
        ("\nforward 1 speed=120", 2, "от 0 до 100"),
        ("forward 1 turbo=1", 1, "скорость=… или угол=…"),
        ("forward abc", 1, "не длительность"),
        ("repeat 2\nstop", 1, "не закрыт"),
        ("end", 1, "без «повтор»"),
        ('text "AT', 1, "кавычка"),
        ("hex ZZ", 1, "hex"),
    ],
)
def test_errors_point_at_line(text, line, fragment):
    with pytest.raises(MacroError) as info:
        parse_macro(text)
    assert info.value.line == line
    assert fragment in info.value.message


def test_send_is_validated(profile):
    with pytest.raises(MacroError, match="строка 2: неизвестная команда"):
        parse_macro("stop\nsend NOPE", lambda text: encode_command(profile, text))


def test_durations():
    assert parse_duration("1.5", 1) == 1.5
    assert parse_duration("1,5с", 1) == 1.5
    assert parse_duration("250ms", 1) == 0.25
    assert parse_duration("250мс", 1) == 0.25
    assert parse_duration("2сек", 1) == 2


def test_bundled_example_macros_are_valid(profile):
    macros = sorted((DEFAULT_PROFILE.parents[1] / "macros").glob("*.txt"))
    assert macros
    for path in macros:
        steps = flatten(parse_macro(path.read_text(encoding="utf-8"), lambda text: encode_command(profile, text)))
        assert steps, path.name


def test_escapes():
    assert decode_escapes(r"AT\r\n") == b"AT\r\n"
    assert decode_escapes(r"\x41\x0a") == b"A\n"
    assert decode_escapes("Привет") == "Привет".encode()
