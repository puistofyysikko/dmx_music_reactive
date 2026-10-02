"""Deterministic offline simulation: drive a Show from a recorded Features stream at a fixed
render rate (no threads, no clock), recording what each fixture would receive."""

from __future__ import annotations

from dataclasses import dataclass

from dmxreactive.analysis.features import Features
from dmxreactive.engine.show import Show


@dataclass
class Tick:
    t: float
    mood: str
    dmx: dict[str, list[int]]  # fixture name -> DMX values
    locked: bool


def simulate(show: Show, feats: list[Features], fps: float = 30.0,
             on_tick=None) -> list[Tick]:
    """`feats` must be in time order. `on_tick(i, t, show)` may inject hotkeys/overrides."""
    out: list[Tick] = []
    dt = 1.0 / fps
    end = feats[-1].t
    j, latest, i = 0, None, 0
    t = dt
    while t <= end:
        while j < len(feats) and feats[j].t <= t:
            latest = feats[j]
            j += 1
        if on_tick:
            on_tick(i, t, show)
        age = (t - latest.t) if latest else 0.0
        show.step(latest, age, dt)
        out.append(Tick(t, show.mood, {f.name: f.dmx_values() for f in show.patch.fixtures},
                        bool(latest and latest.beat_locked)))
        t += dt
        i += 1
    return out
