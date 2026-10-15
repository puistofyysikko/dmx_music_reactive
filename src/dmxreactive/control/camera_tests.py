"""Camera test programs: slow, repeatable sequences to film (GoPro) and measure afterwards.

Every item is announced by a "slate": a 0.3 s white flash of the wash bar, then 0.7 s dark, so the
clip can be cut into items automatically (the flashes are easy to find in the brightness curve).
The printed times are seconds since the start of the output; the first slate comes after `lead_in`.
"""

from __future__ import annotations

from collections.abc import Callable

from dmxreactive.control.walker import Step, Walk
from dmxreactive.dmx.profiles import Fixture, Patch

SLATE_ON_S = 0.3
SLATE_S = 1.0
PROGRAMS = ("derby-motor", "wash-ramp", "wash-pulse", "wash-strobe", "wash-palette")
DERBY_VALUES = (1, 32, 64, 96, 128, 144, 160, 176, 192, 208, 224, 240, 255)
RAMP_LEVELS = (0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
PULSE_MS = (25, 50, 100, 200, 400)  # one frame at 40 Hz = 25 ms


class Script(Walk):
    """A Walk over explicit steps instead of the per-fixture channel walk."""

    def __init__(self, patch: Patch, steps: list[Step],
                 announce: Callable[[str], None] = print):
        self.steps = steps
        self.total = sum(s.duration for s in steps)
        self._announce = announce
        self._index = -1
        patch.blackout()


def _bar(wash: Fixture, level: float, rgb: tuple[float, float, float] = (1, 1, 1)) -> None:
    for n in range(1, 9):
        if wash.has(f"s{n}_r"):
            for ch, v in zip("rgb", rgb, strict=True):
                wash.set(f"s{n}_{ch}", level * v)


def _slate(wash: Fixture) -> Step:
    return Step("  slate", SLATE_S,
                lambda p: _bar(wash, 1.0 if p * SLATE_S < SLATE_ON_S else 0.0),
                lambda: _bar(wash, 0.0))


def build_script(patch: Patch, program: str, *, wash_name: str | None = None,
                 derby_name: str | None = None, hold_s: float = 8.0, lead_in_s: float = 5.0,
                 values: list[int] | None = None) -> Script:
    """Build one of PROGRAMS. Raises KeyError/ValueError if the needed fixture is missing."""
    if program not in PROGRAMS:
        raise ValueError(f"unknown program {program!r}; choose from {', '.join(PROGRAMS)}")
    washes = patch.group("washes")
    wash = patch.get(wash_name) if wash_name else (washes[0] if washes else None)
    if wash is None or not wash.has("s1_r"):
        raise ValueError("camera-test needs an RGB-segment wash bar (the slate flash)")
    steps: list[Step] = [Step("lead-in: start the camera, aim at the wall / fixture",
                              lead_in_s, lambda _p: None)]

    def item(label: str, duration: float, apply: Callable[[float], None],
             finish: Callable[[], None] = lambda: None) -> None:
        steps.append(_slate(wash))
        steps.append(Step(label, duration, apply, finish))

    if program == "derby-motor":
        effects = patch.group("effects")
        derby = patch.get(derby_name) if derby_name else (effects[0] if effects else None)
        if derby is None or not derby.has("motor"):
            raise ValueError("derby-motor needs a Derby fixture with a motor channel")

        def start(v: int) -> Callable[[float], None]:
            def apply(_p: float) -> None:
                derby.set("color", "white")
                derby.set_raw_dmx("motor", v)
            return apply

        for v in values or DERBY_VALUES:
            item(f"{derby.name}: motor DMX {v}", hold_s, start(v), derby.blackout)
    elif program == "wash-ramp":
        for label, rgb in (("white", (1, 1, 1)), ("red", (1, 0, 0)),
                           ("green", (0, 1, 0)), ("blue", (0, 0, 1))):
            for lv in RAMP_LEVELS:
                item(f"{wash.name}: {label} {lv * 100:g} %", min(hold_s, 3.0),
                     lambda _p, lv=lv, rgb=rgb: _bar(wash, lv, rgb), lambda: _bar(wash, 0.0))
    elif program == "wash-palette":
        # every palette colour of the show at full level, then at 25 %, for judging by eye (and
        # on camera) which colours are too dim, too bright or off; raw mapping, no show gains
        from dmxreactive.engine.show import PALETTE, WASH_RGB

        for level in (1.0, 0.25):
            for name in PALETTE:
                item(f"{wash.name}: {name} at {level:.0%}", min(hold_s, 4.0),
                     lambda _p, rgb=WASH_RGB[name], lv=level: _bar(wash, lv, rgb),
                     lambda: _bar(wash, 0.0))
    elif program == "wash-strobe":
        # Alternating 100 ms pulses, 500 ms apart: wash on the second, strobe half a second
        # later. Which light is which is known from the order, so the lag of one relative to
        # the other falls out of the onset intervals (tools/alt_sync.py), no audio needed.
        strobes = patch.group("strobes")
        if not strobes or not strobes[0].has("dimmer"):
            raise ValueError("wash-strobe needs a strobe fixture with a dimmer")
        strobe = strobes[0]
        total = max(hold_s, 30.0)

        def alternate(p: float) -> None:
            phase = (p * total) % 1.0
            _bar(wash, 1.0 if phase < 0.1 else 0.0)
            strobe.set("dimmer", 1.0 if 0.5 <= phase < 0.6 else 0.0)

        item(f"{wash.name} / {strobe.name}: alternating pulses (wash on the second, strobe "
             f"+0.5 s)", total, alternate, lambda: (_bar(wash, 0.0), strobe.blackout()))
    else:  # wash-pulse
        for ms in PULSE_MS:
            for rep in range(1, 4):
                width = max(ms / 1000.0, 0.025)
                item(f"{wash.name}: white pulse {ms} ms ({rep}/3)", width + 1.0,
                     lambda p, w=width, d=width + 1.0: _bar(wash, 1.0 if p * d < w else 0.0),
                     lambda: _bar(wash, 0.0))
    steps.append(Step("end", 1.0, lambda _p: None))
    return Script(patch, steps)
