"""The show: turns analysis `Features` into fixture intents, with scenes, mood switching,
safety limits and manual overrides. Pure logic (no I/O, no clock) so it is fully testable.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from dmxreactive.analysis.features import Features
from dmxreactive.dmx.profiles import Patch
from dmxreactive.engine.primitives import Decay, FlashLimiter, Slew, clamp01, smooth_toward

log = logging.getLogger(__name__)

STALE_S = 1.5  # features older than this are treated as "no audio"
PALETTE = ("red", "orange", "yellow", "green", "cyan", "blue", "purple", "white")


@dataclass(frozen=True)
class ShowConfig:
    beat_lead_ms: float = 30.0  # fire beat effects this much early (pipeline latency)
    max_flash_hz: float = 8.0  # hard cap on flashes/s of dimmer-pulse flashing
    flash_decay_ms: float = 70.0
    master: float = 0.8  # overall brightness ceiling 0..1
    pan_limits: tuple[float, float] = (0.2, 0.8)  # normalised range the head may use
    tilt_limits: tuple[float, float] = (0.3, 0.8)
    scene: str = "auto"  # auto | beat | ambient
    colour_hold_s: float = 1.5  # minimum time between colour-wheel changes
    silence_fade_s: float = 2.0
    flash_gate: float = 0.35  # no beat flash below this bass energy (breakdowns stay dark)
    kick_flash_strength: float = 0.6  # off-grid kicks this strong (and bass this high) flash too
    ambient_flash_rise_db: float = 6.0  # ambient flashes need a real bass transient (dB jump)
    ambient_flash_energy: float = 0.75  # ambient scene flashes only on very strong bass hits...
    ambient_flash_hz: float = 3.0  # ...and never faster than this


@dataclass
class Ctx:
    """Everything a scene needs for one render tick."""

    t: float
    dt: float
    f: Features | None  # None: no usable audio
    silent: bool
    locked: bool
    phase: float  # extrapolated beat phase now, 0..1
    beats_fired: int  # lead-compensated grid beats since the previous tick (0 or 1)
    beat_pos: float  # continuous position in beats
    level: float  # smoothed level 0..1
    bass: float
    kicks: int  # kick events since the previous tick
    kick_strength: float  # strength of the newest kick
    kick_rise_db: float  # bass jump at the newest kick (noise-like content stays low)
    bass_energy: float  # peak-held bass energy vs the track's loud reference, 0..1


Intent = dict[str, dict[str, float | str]]  # fixture name -> channel -> value


class BeatScene:
    """Beat-locked: grid-synced flashes, pulsing mover, per-bar colour, beat-paced motion."""

    name = "beat"

    def __init__(self, patch: Patch, cfg: ShowConfig, limiter: FlashLimiter) -> None:
        self.movers = patch.group("movers")
        self.strobes = patch.group("strobes")
        self.cfg = cfg
        self._limiter = limiter
        self._flash = Decay(cfg.flash_decay_ms / 1000.0)
        self._accent = Decay(0.25)
        self.strobe_enabled = True
        self.flash_count = 0

    def render(self, c: Ctx) -> Intent:
        out: Intent = {}
        cfg = self.cfg
        energy = c.bass_energy
        fired_flash = False
        if self.strobe_enabled:
            # 1) a predicted grid beat flashes, unless the bass has dropped out (breakdown)
            if c.beats_fired and energy >= cfg.flash_gate and self._limiter.try_fire(c.t):
                self._flash.trigger(0.5 + 0.5 * clamp01((energy - cfg.flash_gate) / 0.5))
                fired_flash = True
            # 2) a strong bass hit between grid beats (syncopation, drop) flashes too
            elif (c.kicks and c.kick_strength >= cfg.kick_flash_strength
                  and energy >= cfg.kick_flash_strength and self._limiter.try_fire(c.t)):
                self._flash.trigger(0.8)
                fired_flash = True
        if fired_flash:
            self.flash_count += 1
        flash = self._flash.step(c.dt)
        if c.beats_fired and energy >= cfg.flash_gate or fired_flash:
            self._accent.trigger(clamp01(energy / 0.6))
        accent = self._accent.step(c.dt)
        for f in self.strobes:
            out[f.name] = {"dimmer": flash if self.strobe_enabled else 0.0}
        bar = int(c.beat_pos // 8)
        pan_lo, pan_hi = cfg.pan_limits
        tilt_lo, tilt_hi = cfg.tilt_limits
        for i, f in enumerate(self.movers):
            pan = 0.5 + 0.5 * math.sin(2 * math.pi * (c.beat_pos / 16.0 + 0.25 * i))
            tilt = 0.5 + 0.5 * math.sin(2 * math.pi * (c.beat_pos / 11.0 + 0.4 * i))
            out[f.name] = {
                "dimmer": 0.2 + 0.8 * max(accent, 0.5 * clamp01(c.level)),
                "pan": pan_lo + pan * (pan_hi - pan_lo),
                "tilt": tilt_lo + tilt * (tilt_hi - tilt_lo),
                "speed": 1.0,
                "color": PALETTE[(bar + i) % len(PALETTE)],
            }
        return out


class AmbientScene:
    """No reliable beat: breathe with the music's loudness, slow wander, slow colour drift.
    Only a very strong bass hit (a drop the beat tracker has not locked onto yet) flashes."""

    name = "ambient"

    def __init__(self, patch: Patch, cfg: ShowConfig, limiter: FlashLimiter) -> None:
        self.movers = patch.group("movers")
        self.strobes = patch.group("strobes")
        self.cfg = cfg
        self._limiter = limiter  # the shared hard cap
        self._own = FlashLimiter(cfg.ambient_flash_hz)
        self._flash = Decay(cfg.flash_decay_ms / 1000.0)
        self.strobe_enabled = True
        self.flash_count = 0

    def render(self, c: Ctx) -> Intent:
        cfg = self.cfg
        if (self.strobe_enabled and c.kicks and c.kick_strength >= cfg.ambient_flash_energy
                and c.kick_rise_db >= cfg.ambient_flash_rise_db
                and c.bass_energy >= cfg.ambient_flash_energy and self._own.try_fire(c.t)
                and self._limiter.try_fire(c.t)):
            self._flash.trigger(0.7)
            self.flash_count += 1
        flash = self._flash.step(c.dt)
        out: Intent = {f.name: {"dimmer": flash if self.strobe_enabled else 0.0}
                       for f in self.strobes}
        pan_lo, pan_hi = cfg.pan_limits
        tilt_lo, tilt_hi = cfg.tilt_limits
        for i, f in enumerate(self.movers):
            pan = 0.5 + 0.5 * math.sin(2 * math.pi * (c.t / 45.0 + 0.3 * i))
            tilt = 0.5 + 0.5 * math.sin(2 * math.pi * (c.t / 31.0 + 0.5 + 0.2 * i))
            out[f.name] = {
                "dimmer": 0.15 + 0.5 * clamp01(c.level),
                "pan": pan_lo + pan * (pan_hi - pan_lo),
                "tilt": tilt_lo + tilt * (tilt_hi - tilt_lo),
                "speed": 0.6,
                "color": PALETTE[(int(c.t // 20) + i) % len(PALETTE)],
            }
        return out


@dataclass
class _MoverState:
    pan: Slew = field(default_factory=lambda: Slew(0.8))
    tilt: Slew = field(default_factory=lambda: Slew(0.8))
    colour: str = ""
    colour_t: float = -1e9


class Show:
    def __init__(self, patch: Patch, cfg: ShowConfig | None = None) -> None:
        self.patch = patch
        self.cfg = cfg or ShowConfig()
        cap = self.cfg.max_flash_hz
        self._limiter = FlashLimiter(cap)
        self._beat = BeatScene(patch, self.cfg, self._limiter)
        self._ambient = AmbientScene(patch, self.cfg, self._limiter)
        self._scenes = {"beat": self._beat, "ambient": self._ambient}
        self.mood = "ambient"  # the scene we are heading to
        self._from = "ambient"
        self._mix = 1.0  # 0 = fully `_from`, 1 = fully `mood`
        self._mood_since = 0.0
        self._lock_run = 0.0  # seconds the lock state has been stable
        self._last_locked = False
        self.override = self.cfg.scene if self.cfg.scene in self._scenes else "auto"
        self.blackout = False
        self.master = self.cfg.master
        self._master_now = 0.0
        self._silent_for = 0.0
        self.t = 0.0
        self._level = 0.0
        self._bass = 0.0
        self._prev_adj = 0.0
        self._last_kick_count = 0
        self._last_beat_fire = -1e9
        self._beat_pos = 0.0
        self._bpm = 0.0
        self._state = {f.name: _MoverState() for f in patch.fixtures}
        self.strobe_enabled = True

    @property
    def flashes(self) -> int:
        return self._beat.flash_count + self._ambient.flash_count

    # -- controls -------------------------------------------------------------------
    def command(self, key: str) -> str:
        """Handle a hotkey; returns a short status message."""
        if key == "b":
            self.blackout = not self.blackout
            return "BLACKOUT" if self.blackout else "blackout off"
        if key == "s":
            self.strobe_enabled = not self.strobe_enabled
            self._beat.strobe_enabled = self._ambient.strobe_enabled = self.strobe_enabled
            return f"strobe light {'on' if self.strobe_enabled else 'OFF'}"
        if key == "a":
            self.override = "auto"
            return "scene: auto"
        if key in ("1", "2"):
            self.override = "beat" if key == "1" else "ambient"
            return f"scene: {self.override} (forced)"
        if key in ("+", "="):
            self.master = min(1.0, self.master + 0.1)
            return f"master {self.master:.0%}"
        if key in ("-", "_"):
            self.master = max(0.0, self.master - 0.1)
            return f"master {self.master:.0%}"
        return ""

    # -- one tick ---------------------------------------------------------------------
    def step(self, f: Features | None, age_s: float, dt: float) -> None:
        """Advance the show by dt seconds. `f` is the newest analysis (None if none yet),
        `age_s` how long ago that analysis frame was produced."""
        self.t += dt
        stale = f is None or age_s > STALE_S
        silent = stale or f.silent
        ctx = self._context(f, age_s, dt, silent, stale)
        self._update_mood(ctx, dt)
        intents = self._blend(ctx, dt)
        self._update_master(silent, dt)
        self._apply(intents, dt)

    def _context(self, f: Features | None, age_s: float, dt: float, silent: bool,
                 stale: bool) -> Ctx:
        phase, fired, kicks, locked, strength, bass, rise = 0.0, 0, 0, False, 0.0, 0.0, 0.0
        if f is not None and not stale:
            locked = f.beat_locked and f.bpm > 0
            if f.bpm > 0:
                self._bpm = f.bpm
                phase = (f.beat_phase + age_s * f.bpm / 60.0) % 1.0
                lead = self.cfg.beat_lead_ms / 1000.0 * f.bpm / 60.0
                adj = (phase + lead) % 1.0
                if self._prev_adj - adj > 0.5 and locked:
                    period = 60.0 / f.bpm
                    if self.t - self._last_beat_fire >= 0.6 * period:
                        fired = 1
                        self._last_beat_fire = self.t
                self._prev_adj = adj
            kicks = f.kick_count - self._last_kick_count
            self._last_kick_count = f.kick_count
            strength = f.last_kick_strength
            rise = f.last_kick_rise_db
            bass = f.bass_energy
            self._level = smooth_toward(self._level, f.level, dt, 0.6)
        # Beat position integrates smoothly (it paces the sweeps); a fired grid beat only
        # nudges it toward the nearest whole beat, so grid re-syncs never make the head jump.
        if self._bpm > 0:
            self._beat_pos += dt * self._bpm / 60.0
            if fired:
                self._beat_pos += 0.25 * (round(self._beat_pos) - self._beat_pos)
        return Ctx(self.t, dt, f, silent, locked, phase, fired, self._beat_pos, self._level,
                   bass, max(0, kicks), strength, rise, bass)

    def _update_mood(self, c: Ctx, dt: float) -> None:
        if self.override != "auto":
            target = self.override
        else:
            if c.locked == self._last_locked:
                self._lock_run += dt
            else:
                self._lock_run, self._last_locked = 0.0, c.locked
            target = self.mood
            held = self.t - self._mood_since
            if self.mood == "ambient" and c.locked and self._lock_run >= 1.5 and held >= 3.0:
                target = "beat"
            elif self.mood == "beat" and not c.locked and self._lock_run >= 10.0 and held >= 6.0:
                target = "ambient"
        if target != self.mood:
            self._from, self.mood = self.mood, target
            self._mix, self._mood_since = 0.0, self.t
            log.info("scene -> %s", target)
        self._mix = min(1.0, self._mix + dt / 1.5)

    def _blend(self, c: Ctx, dt: float) -> Intent:
        new = self._scenes[self.mood].render(c)
        if self._mix >= 1.0 or self._from == self.mood:
            return new
        old = self._scenes[self._from].render(c)
        mixed: Intent = {}
        for name in new.keys() | old.keys():
            a, b = old.get(name, {}), new.get(name, {})
            ch: dict[str, float | str] = {}
            for key in a.keys() | b.keys():
                va, vb = a.get(key, b.get(key)), b.get(key, a.get(key))  # hold if one side lacks it
                if isinstance(va, str) or isinstance(vb, str):
                    ch[key] = vb if self._mix > 0.5 else va
                else:
                    ch[key] = va + (vb - va) * self._mix
            mixed[name] = ch
        return mixed

    def _update_master(self, silent: bool, dt: float) -> None:
        self._silent_for = self._silent_for + dt if silent else 0.0
        target = 0.0 if self._silent_for >= 0.5 else self.master
        tau = self.cfg.silence_fade_s / 3 if target == 0.0 else 0.3
        self._master_now = smooth_toward(self._master_now, target, dt, tau)

    def _apply(self, intents: Intent, dt: float) -> None:
        pan_lo, pan_hi = self.cfg.pan_limits
        tilt_lo, tilt_hi = self.cfg.tilt_limits
        for fx in self.patch.fixtures:
            st = self._state[fx.name]
            want = intents.get(fx.name, {})
            if "dimmer" in want and fx.has("dimmer"):
                level = 0.0 if self.blackout else clamp01(float(want["dimmer"])) * self._master_now
                fx.set("dimmer", level)
            if fx.has("strobe"):
                fx.set("strobe", 0.0)  # flashes are dimmer pulses; built-in strobe stays off
            if "pan" in want and fx.has("pan"):
                pan = _clip(float(want["pan"]), pan_lo, pan_hi)
                fx.set("pan", clamp01(st.pan.step(pan, dt)))
            if "tilt" in want and fx.has("tilt"):
                tilt = _clip(float(want["tilt"]), tilt_lo, tilt_hi)
                fx.set("tilt", clamp01(st.tilt.step(tilt, dt)))
            if "speed" in want and fx.has("speed"):
                fx.set("speed", clamp01(float(want["speed"])))
            if "color" in want and fx.has("color"):
                colour = str(want["color"])
                if colour != st.colour and self.t - st.colour_t >= self.cfg.colour_hold_s:
                    st.colour, st.colour_t = colour, self.t
                if st.colour:
                    fx.set("color", st.colour)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
