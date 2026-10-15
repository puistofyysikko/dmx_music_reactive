"""Channel walker: steps every patched fixture through its functions at safe levels."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from dmxreactive.dmx.profiles import Fixture, Patch

WALK_STROBE_HZ = 3.0  # well under any photosensitivity-critical rate
WALK_STROBE_NORM = 0.1  # used when the profile has no Hz mapping


@dataclass
class Step:
    label: str
    duration: float
    apply: Callable[[float], None]  # called with progress 0..1 while the step runs
    finish: Callable[[], None] = lambda: None  # called once when the step ends


def fixture_steps(f: Fixture, step_s: float) -> list[Step]:
    """Build the steps for one fixture. Nothing is mutated until the steps are played."""
    steps: list[Step] = []
    if f.has("dimmer"):
        steps.append(Step(f"{f.name}: dimmer fade up", step_s,
                          lambda p: f.set("dimmer", 0.5 * p), lambda: f.set("dimmer", 0.5)))
    for axis in ("pan", "tilt"):
        if f.has(axis):
            steps.append(Step(
                f"{f.name}: {axis} sweep (slow, centre +/- 25 %)", 2 * step_s,
                lambda p, a=axis: f.set(a, 0.5 + 0.25 * math.sin(2 * math.pi * p)),
                lambda a=axis: f.set(a, 0.5),
            ))
    if f.has("color"):
        for slot in f.profile.channels["color"].slots:
            steps.append(Step(f"{f.name}: colour {slot}", step_s / 2,
                              lambda _p, s=slot: f.set("color", s)))
        steps[-1].finish = lambda: f.set("color", "white")
    if f.has("s1_r"):  # RGB segment bar: each segment alone (checks order), then whole-bar colours
        segs = [n for n in range(1, 9) if f.has(f"s{n}_r")]

        def seg_rgb(n: int, rgb: tuple[float, float, float]) -> None:
            for ch, v in zip("rgb", rgb, strict=True):
                f.set(f"s{n}_{ch}", v)

        for n in segs:
            steps.append(Step(f"{f.name}: segment {n} white", step_s / 2,
                              lambda _p, n=n: seg_rgb(n, (0.5, 0.5, 0.5)),
                              lambda n=n: seg_rgb(n, (0.0, 0.0, 0.0))))
        for label, rgb in (("red", (0.5, 0, 0)), ("green", (0, 0.5, 0)), ("blue", (0, 0, 0.5))):
            steps.append(Step(f"{f.name}: all segments {label}", step_s / 2,
                              lambda _p, rgb=rgb: [seg_rgb(n, rgb) for n in segs],
                              lambda: [seg_rgb(n, (0.0, 0.0, 0.0)) for n in segs]))
    if f.has("motor"):
        for slot in f.profile.channels["motor"].slots:
            steps.append(Step(f"{f.name}: motor {slot}", step_s,
                              lambda _p, s=slot: f.set("motor", s),
                              lambda: f.set("motor", "hold")))
    if f.has("white") and "full" in f.profile.channels["white"].slots:
        steps.append(Step(f"{f.name}: white LEDs on", step_s / 2,
                          lambda _p: f.set("white", "full"), lambda: f.set("white", "off")))
    if f.has("strobe"):
        ch = f.profile.channels["strobe"]
        if ch.hz_range or ch.period_ms:
            steps.append(Step(f"{f.name}: strobe {WALK_STROBE_HZ:g} Hz", step_s,
                              lambda _p: f.set_hz("strobe", WALK_STROBE_HZ),
                              lambda: f.set("strobe", 0.0)))
        else:
            steps.append(Step(f"{f.name}: strobe (norm {WALK_STROBE_NORM}, Hz unknown)", step_s,
                              lambda _p: f.set("strobe", WALK_STROBE_NORM),
                              lambda: f.set("strobe", 0.0)))
    if steps:
        prev = steps[-1].finish
        steps[-1].finish = lambda: (prev(), f.blackout())
    return steps


class Walk:
    """Plays steps by elapsed time; call update(t) each frame."""

    def __init__(self, patch: Patch, step_s: float = 2.0, announce: Callable[[str], None] = print):
        self.steps = [s for f in patch.fixtures for s in fixture_steps(f, step_s)]
        self.total = sum(s.duration for s in self.steps)
        self._announce = announce
        self._index = -1
        patch.blackout()

    def update(self, t: float) -> None:
        start = 0.0
        for i, step in enumerate(self.steps):
            end = start + step.duration
            if t < end:
                while self._index < i:  # finish skipped/previous steps exactly once
                    if self._index >= 0:
                        self.steps[self._index].finish()
                    self._index += 1
                    self._announce(self.steps[self._index].label)
                step.apply((t - start) / step.duration)
                return
            start = end
        if self._index < len(self.steps):  # past the end: finish the last step once
            if self._index >= 0:
                self.steps[self._index].finish()
            self._index = len(self.steps)
