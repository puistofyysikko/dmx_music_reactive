"""Small building blocks for effects: envelopes, slew limiting, flash rate limiting."""

from __future__ import annotations

import math


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def smooth_toward(current: float, target: float, dt: float, tau: float) -> float:
    """One-pole smoothing with time constant `tau` seconds (tau <= 0: jump)."""
    if tau <= 0:
        return target
    return current + (target - current) * (1.0 - math.exp(-dt / tau))


class Decay:
    """A flash envelope: jumps to a level on trigger, then decays exponentially.

    With `full_first_frame`, the frame that follows a trigger is output at the full triggered
    level (decay starts on the next frame); by default the decay is applied first, so the
    first frame shows ~60 % of the level at a 30 Hz render rate.
    """

    def __init__(self, tau_s: float, full_first_frame: bool = False) -> None:
        self.tau = tau_s
        self.value = 0.0
        self._full_first = full_first_frame

    def trigger(self, level: float = 1.0) -> None:
        self.value = max(self.value, level)

    def step(self, dt: float) -> float:
        shown = self.value
        self.value *= math.exp(-dt / self.tau)
        if self.value < 1e-3:
            self.value = 0.0
        return shown if self._full_first else self.value


class Slew:
    """Limits how fast a value may change (units per second); first value passes unchanged."""

    def __init__(self, rate_per_s: float) -> None:
        self.rate = rate_per_s
        self.value: float | None = None

    def step(self, target: float, dt: float) -> float:
        if self.value is None:
            self.value = target
        else:
            max_delta = self.rate * dt
            self.value += max(-max_delta, min(max_delta, target - self.value))
        return self.value


class FlashLimiter:
    """Allows at most `max_hz` flashes per second (hard safety cap, enforced for every flash)."""

    def __init__(self, max_hz: float) -> None:
        self.min_interval = 1.0 / max_hz
        self._last = -1e9

    def try_fire(self, t: float) -> bool:
        if t - self._last >= self.min_interval:
            self._last = t
            return True
        return False


class HoldDecay:
    """A pulse that holds its full level for `hold_s` and then decays exponentially.

    Output is the full level on the first frame after a trigger, so even a short hold is
    visible at a 30 Hz render rate (the moving head's dimmer is slow: it needs ~100 ms+)."""

    def __init__(self, hold_s: float, tau_s: float) -> None:
        self.hold, self.tau = hold_s, tau_s
        self.level = 0.0
        self._age = 1e9

    def trigger(self, level: float = 1.0) -> None:
        self.level, self._age = max(self.level if self._age < self.hold else 0.0, level), 0.0

    def step(self, dt: float) -> float:
        if self.level <= 0.0:
            return 0.0
        age = self._age
        self._age += dt
        if age < self.hold:
            return self.level
        value = self.level * math.exp(-(age - self.hold) / self.tau)
        if value < 1e-3:
            self.level = 0.0
            return 0.0
        return value
