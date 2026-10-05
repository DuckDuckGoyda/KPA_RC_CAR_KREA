"""Driving math: keyboard ramping and mixing into command values.

``throttle`` and ``steer`` are normalised to -1..1 (+ = forward / right).
``mix`` turns them into the integers the drive templates use:
{speed} {angle} for servo steering, {left} {right} for differential drive.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..protocol.profile import DriveSettings


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _approach(current: float, target: float, step: float) -> float:
    if current < target:
        return min(current + step, target)
    return max(current - step, target)


@dataclass
class DriveModel:
    accel: float = 2.0
    decel: float = 3.0
    steer_rate: float = 5.0
    center_rate: float = 8.0
    throttle: float = 0.0
    steer: float = 0.0

    @classmethod
    def from_settings(cls, settings: DriveSettings, throttle_gain: float = 1.0, steer_gain: float = 1.0) -> DriveModel:
        model = cls()
        model.configure(settings, throttle_gain, steer_gain)
        return model

    def configure(self, settings: DriveSettings, throttle_gain: float = 1.0, steer_gain: float = 1.0) -> None:
        """Take the profile's rates, scaled by the user's sensitivity; keeps the current position.

        A higher throttle sensitivity also releases faster, but a lower one never
        makes letting go slower than the profile says.
        """
        self.accel = settings.accel * throttle_gain
        self.decel = settings.decel * max(1.0, throttle_gain)
        self.steer_rate = settings.steer_rate * steer_gain
        self.center_rate = settings.center_rate * steer_gain

    def reset(self) -> None:
        self.throttle = 0.0
        self.steer = 0.0

    def set(self, throttle: float, steer: float) -> None:
        """Jump straight to a value (analog input needs no ramp)."""
        self.throttle = _clamp(throttle)
        self.steer = _clamp(steer)

    def update(self, target_throttle: float, target_steer: float, dt: float) -> None:
        target_throttle = _clamp(target_throttle)
        target_steer = _clamp(target_steer)

        t = self.throttle
        if t * target_throttle < 0:
            # Reversing: bleed off speed first, at the braking rate.
            self.throttle = _approach(t, 0.0, self.decel * dt)
        elif abs(target_throttle) > abs(t):
            self.throttle = _approach(t, target_throttle, self.accel * dt)
        else:
            self.throttle = _approach(t, target_throttle, self.decel * dt)

        rate = self.center_rate if target_steer == 0 else self.steer_rate
        self.steer = _approach(self.steer, target_steer, rate * dt)

    @property
    def idle(self) -> bool:
        return self.throttle == 0.0 and self.steer == 0.0


@dataclass(frozen=True)
class DriveValues:
    speed: int = 0
    angle: int = 0
    left: int = 0
    right: int = 0
    throttle: float = 0.0
    steer: float = 0.0

    @property
    def is_zero(self) -> bool:
        return self.speed == 0 and self.angle == 0 and self.left == 0 and self.right == 0

    def template_values(self) -> dict[str, int]:
        return {"speed": self.speed, "angle": self.angle, "left": self.left, "right": self.right}


def mix(throttle: float, steer: float, settings: DriveSettings, speed_limit: float = 1.0) -> DriveValues:
    throttle = _clamp(throttle)
    steer = _clamp(steer)
    limit = _clamp(speed_limit, 0.0, 1.0)

    speed_sign = -1 if settings.invert_speed else 1
    angle_sign = -1 if settings.invert_angle else 1

    speed = round(throttle * limit * settings.max_speed) * speed_sign
    angle = round(steer * settings.max_angle) * angle_sign

    # Differential: left = throttle + steer, right = throttle - steer,
    # scaled back into range so full throttle + full steer keeps its ratio.
    left_f = throttle + steer * angle_sign
    right_f = throttle - steer * angle_sign
    peak = max(1.0, abs(left_f), abs(right_f))
    left = round(left_f / peak * limit * settings.max_speed) * speed_sign
    right = round(right_f / peak * limit * settings.max_speed) * speed_sign

    return DriveValues(speed=speed, angle=angle, left=left, right=right, throttle=throttle, steer=steer)
