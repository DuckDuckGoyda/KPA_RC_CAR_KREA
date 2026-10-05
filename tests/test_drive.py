import pytest

from rc_controller.control.drive import DriveModel, mix
from rc_controller.protocol.profile import DriveSettings, GamepadSettings
from rc_controller.runtime.gamepad import GamepadState, map_gamepad


def run(model: DriveModel, throttle: float, steer: float, seconds: float, dt: float = 0.01) -> None:
    for _ in range(round(seconds / dt)):
        model.update(throttle, steer, dt)


def test_ramp_up_and_release():
    model = DriveModel(accel=2.0, decel=4.0)
    run(model, 1, 0, 0.25)
    assert model.throttle == pytest.approx(0.5)
    run(model, 1, 0, 1.0)
    assert model.throttle == 1.0
    run(model, 0, 0, 0.1)
    assert model.throttle == pytest.approx(0.6)
    run(model, 0, 0, 1.0)
    assert model.throttle == 0.0


def test_reverse_brakes_through_zero():
    model = DriveModel(accel=1.0, decel=5.0)
    model.set(1.0, 0.0)
    run(model, -1, 0, 0.2)  # braking at the faster rate reaches zero in 0.2 s
    assert model.throttle == pytest.approx(0.0)
    run(model, -1, 0, 0.5)  # then accelerates backwards at the normal rate
    assert model.throttle == pytest.approx(-0.5)


def test_steering_recenters_faster():
    model = DriveModel(steer_rate=2.0, center_rate=10.0)
    run(model, 0, 1, 0.5)
    assert model.steer == 1.0
    run(model, 0, 0, 0.12)
    assert model.steer == 0.0


def test_mix_speed_angle_with_limit_and_inversion():
    settings = DriveSettings(max_speed=127, max_angle=100)
    values = mix(1.0, -0.5, settings, speed_limit=0.5)
    assert (values.speed, values.angle) == (64, -50)
    inverted = mix(1.0, -0.5, DriveSettings(max_speed=127, max_angle=100, invert_speed=True, invert_angle=True))
    assert (inverted.speed, inverted.angle) == (-127, 50)


def test_mix_differential():
    settings = DriveSettings(mode="differential", max_speed=100)
    assert (mix(1.0, 0.0, settings).left, mix(1.0, 0.0, settings).right) == (100, 100)
    pivot = mix(0.0, 1.0, settings)
    assert (pivot.left, pivot.right) == (100, -100)
    arc = mix(1.0, 1.0, settings)  # full throttle + full right: right side stops
    assert (arc.left, arc.right) == (100, 0)
    limited = mix(0.0, 1.0, settings, speed_limit=0.3)
    assert (limited.left, limited.right) == (30, -30)


def test_sensitivity_scales_rates_but_never_slows_release():
    settings = DriveSettings(accel=2.0, decel=3.0, steer_rate=5.0, center_rate=8.0)
    model = DriveModel.from_settings(settings, throttle_gain=0.5, steer_gain=2.0)
    assert (model.accel, model.decel, model.steer_rate, model.center_rate) == (1.0, 3.0, 10.0, 16.0)
    model.set(0.4, 0.2)
    model.configure(settings, throttle_gain=3.0)  # slider moved while driving: position kept
    assert (model.throttle, model.steer) == (0.4, 0.2)
    assert (model.accel, model.decel) == (6.0, 9.0)


def test_gamepad_auto_throttle_takes_triggers_and_stick():
    settings = GamepadSettings(deadzone=0.1)
    assert map_gamepad(GamepadState(ly=1.0), settings) == (1.0, 0.0)
    assert map_gamepad(GamepadState(ly=-1.0, lx=-1.0), settings) == (-1.0, -1.0)
    assert map_gamepad(GamepadState(rt=1.0), settings) == (1.0, 0.0)
    assert map_gamepad(GamepadState(rt=1.0, ly=1.0), settings) == (1.0, 0.0)  # clamped
    triggers_only = GamepadSettings(throttle="triggers", deadzone=0.1)
    assert map_gamepad(GamepadState(ly=1.0), triggers_only) == (0.0, 0.0)
