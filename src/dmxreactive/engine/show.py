"""The show: turns analysis `Features` into fixture intents, with scenes, mood switching,
safety limits and manual overrides. Pure logic (no I/O, no clock) so it is fully testable.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from dmxreactive.analysis.features import Features
from dmxreactive.dmx.profiles import Patch
from dmxreactive.engine.primitives import (
    Decay,
    FlashLimiter,
    HoldDecay,
    Slew,
    clamp01,
    smooth_toward,
)

log = logging.getLogger(__name__)

STALE_S = 1.5  # features older than this are treated as "no audio"
PALETTE = ("red", "orange", "yellow", "green", "cyan", "blue", "purple", "white")
# Wall wash (RGB bar): palette colour -> RGB at full level
WASH_RGB = {
    "red": (1.0, 0.0, 0.0), "orange": (1.0, 0.25, 0.0), "yellow": (1.0, 0.7, 0.0),
    "green": (0.0, 1.0, 0.0), "cyan": (0.0, 0.8, 1.0), "blue": (0.0, 0.0, 1.0),
    "purple": (0.6, 0.0, 1.0), "white": (1.0, 1.0, 1.0),
}
# Derby effect (colour combinations are fixed slots, no dimming): palette colour -> slot
DERBY_COLOUR = {
    "red": "red", "orange": "red_green", "yellow": "red_green", "green": "green",
    "cyan": "green_blue", "blue": "blue", "purple": "red_blue", "white": "white",
}


# Wear-free modes: "full" = everything moves as designed; "static" = the moving head is parked
# with its gobo and colour wheels fixed and the Derby's motor stopped (LEDs, dimmer and the
# built-in strobe still work); "off" = additionally the moving head and the Derby stay dark.
MECHANICS = ("full", "static", "off")


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
    light_delay_ms: float = 0.0  # delay the lights (e.g. +150..250 for a Bluetooth speaker)
    flash_gate: float = 0.35  # no beat flash below this bass energy (breakdowns stay dark)
    kick_flash_strength: float = 0.6  # off-grid kicks this strong (and bass this high) flash too
    ambient_flash_rise_db: float = 6.0  # ambient flashes need a real bass transient (dB jump)
    ambient_flash_energy: float = 0.75  # ambient scene flashes only on very strong bass hits...
    ambient_flash_hz: float = 3.0  # ...and never faster than this
    drive_kick_rate: float = 2.0  # kicks/s (smoothed) that mark very energetic, kick-dense music
    drive_energy: float = 0.2  # ...together with at least this bass energy (distorted kicks
    #                           keep much of their punch above the 20-150 Hz band)
    drive_flash_gate: float = 0.25  # in the drive scene the kick events are the evidence
    drive_min_confidence: float = 0.15  # ...and some periodicity (rules out pure noise)
    gobo_mode: str = "auto"  # auto: gobo changes on bar boundaries (see palette); off: open
    gobo_palette: tuple[str, ...] = ("open", "dots", "star", "swirl", "petals", "mosaic")
    gobo_hold_s: float = 5.0  # minimum time between gobo changes (the wheel is mechanical)
    bar_min_confidence: float = 0.35  # trust the detected bar position only above this
    mover_flash_every: int = 4  # every Nth flash event the moving head flashes too (0 = never)
    mover_flash_mode: str = "strobe"  # "strobe": burst the head's built-in strobe (crisp);
    #                                   "dimmer": pulse its slow dimmer (slew ~170 ms full scale)
    mover_flash_ms: float = 250.0  # length of a strobe-mode burst (~2 flashes at 8 Hz)
    mover_flash_hz: float = 8.0  # strobe rate during a burst (the profile's safety cap applies)
    mover_flash_hold_ms: float = 200.0  # dimmer mode: hold flashes this long
    drop_duration_s: float = 1.5  # length of the drop blast (strobe stutter, then a fade)
    drop_blackout_every_s: float = 90.0  # a drop may instead go DARK (only the strobe flashes,
    #                                      then the head pops back) at most this often; 0 = never
    drop_blackout_s: float = 1.5  # how long the dark part lasts
    drop_dip: float = 0.25  # a breakdown = bass energy this far below the recent loud reference
    drop_min_gap_s: float = 10.0  # at most one drop blast per this many seconds
    breath_period_s: float = 7.0  # ambient scene: the moving head's brightness breathes at this
    breath_depth: float = 0.6  # ...dipping to (1 - depth) of its level at the bottom
    derby_motor_ambient: str = "slow"  # Derby beam motor slot (see its profile) per scene
    derby_motor_beat: str = "medium"
    wash_gain: float = 1.0  # the wash's own brightness factor on top of `master`
    wash_gamma: float = 1.0  # >1 darkens the low end (2 ~ perceptual); 1 = linear DMX
    wash_balance: tuple[float, float, float] = (1.0, 1.0, 1.0)  # per-channel gain (white balance)
    wash_colours: tuple[tuple[str, tuple[float, float, float]], ...] = ()  # RGB overrides
    mechanics: str = "full"  # wear-free modes: full | static (no motors/wheels move) | off
    park_pan: float = 0.5  # static mode: where the moving head is parked (within pan_limits)
    park_tilt: float = 0.5
    static_colour: str = "white"  # static mode: the colour wheel stays here


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
    energetic: bool  # kick-dense and bass-heavy (frenchcore, hardcore, drops)
    drop: bool  # bass returned strongly after >= 1.5 s of little bass (a drop), once
    bar_beat: int = -1  # position in the bar of the beat that just fired (0 = the one), -1 unknown
    bar_known: bool = False  # the bar position is confident enough to act on
    bar_no: int = 0  # bars seen since the bar position became known


Intent = dict[str, dict[str, float | str]]  # fixture name -> channel -> value


def pick_gobo(cfg: ShowConfig, step: int, offset: int = 0) -> str:
    """The gobo for the `step`-th phrase (the palette cycles); "open" when gobos are off."""
    if cfg.gobo_mode == "off" or not cfg.gobo_palette:
        return "open"
    return cfg.gobo_palette[(step + offset) % len(cfg.gobo_palette)]


class BeatScene:
    """Beat-locked: grid-synced flashes, pulsing mover, per-bar colour, beat-paced motion.

    With `kick_driven=True` (the "drive" scene) the events are the detected kicks themselves
    instead of the predicted grid, and the pacing clock is plain time: for kick-dense music
    (frenchcore, hardcore) where no steady tempo grid can be locked.
    """

    def __init__(self, patch: Patch, cfg: ShowConfig, limiter: FlashLimiter,
                 kick_driven: bool = False) -> None:
        self.kick_driven = kick_driven
        self.name = "drive" if kick_driven else "beat"
        self.movers = patch.group("movers")
        self.strobes = patch.group("strobes")
        self.washes = patch.group("washes")
        self.effects = patch.group("effects")
        self.cfg = cfg
        self._limiter = limiter
        self._flash = Decay(cfg.flash_decay_ms / 1000.0)
        self._seg = [Decay(0.3) for _ in range(4)]  # wash segment pulses (chase)
        self._derby_on = False
        self._accent = Decay(0.25)
        self._mover_flash = HoldDecay(cfg.mover_flash_hold_ms / 1000.0, 0.12)
        self._burst_until = -1.0
        self._bar_known = False
        self._bar_one = False
        self._now = 0.0
        self._events = 0
        self.strobe_enabled = True
        self.flash_count = 0

    def _count_event(self) -> None:
        """Every Nth flash event the moving head flashes along."""
        self._events += 1
        every = self.cfg.mover_flash_every
        # with a trusted bar position the moving head flashes on the ONE; otherwise every Nth
        on_one = self._bar_one if self._bar_known else (every and self._events % every == 0)
        if every and on_one:
            if self.cfg.mover_flash_mode == "strobe":
                self._burst_until = self._now + self.cfg.mover_flash_ms / 1000.0
            else:
                self._mover_flash.trigger(1.0)

    def render(self, c: Ctx) -> Intent:
        out: Intent = {}
        cfg = self.cfg
        self._now = c.t
        self._bar_known = c.bar_known and not self.kick_driven
        self._bar_one = c.bar_beat == 0
        energy = c.bass_energy
        fired_flash = False
        beat_now = bool(c.kicks and c.kick_strength >= 0.4) if self.kick_driven else bool(
            c.beats_fired)
        if self.strobe_enabled:
            # 1) a predicted grid beat (drive scene: a detected kick) flashes, unless the bass
            #    has dropped out (breakdown)
            gate = cfg.drive_flash_gate if self.kick_driven else cfg.flash_gate
            if beat_now and energy >= gate and self._limiter.try_fire(c.t):
                level = 0.6 + 0.4 * clamp01((energy - gate) / 0.5)
                if self._bar_known and not self.kick_driven:
                    level *= 1.0 if self._bar_one else 0.8  # the ONE hits hardest
                self._flash.trigger(level)
                fired_flash = True
                self._count_event()
            # 2) a strong bass hit between grid beats (syncopation) flashes too
            elif (not self.kick_driven and c.kicks and c.kick_strength >= cfg.kick_flash_strength
                  and energy >= cfg.kick_flash_strength and self._limiter.try_fire(c.t)):
                self._flash.trigger(0.8)
                fired_flash = True
                self._count_event()
        if fired_flash:
            self.flash_count += 1
        flash = self._flash.step(c.dt)
        if fired_flash:
            self._accent.trigger(max(0.6, clamp01(energy / 0.6)))
            if self._bar_known and self._bar_one:
                for d in self._seg:
                    d.trigger(1.0)  # the ONE lights the whole bar
            else:
                self._seg[self._events % 4].trigger(0.9)  # otherwise one segment per beat
        accent = self._accent.step(c.dt)
        mover_flash = self._mover_flash.step(c.dt)
        burst = c.t < self._burst_until
        if burst:
            mover_flash = max(mover_flash, 0.9)  # flashes need light to flash: lift the dimmer
        for f in self.strobes:
            out[f.name] = {"dimmer": flash if self.strobe_enabled else 0.0}
        pos = c.t * 2.0 if self.kick_driven else c.beat_pos  # drive: pseudo-beats at 120/min
        bar = (c.bar_no // 2) if self._bar_known else int(pos // 8)
        pan_lo, pan_hi = cfg.pan_limits
        tilt_lo, tilt_hi = cfg.tilt_limits
        for i, f in enumerate(self.movers):
            pan = 0.5 + 0.5 * math.sin(2 * math.pi * (pos / 16.0 + 0.25 * i))
            tilt = 0.5 + 0.5 * math.sin(2 * math.pi * (pos / 11.0 + 0.4 * i))
            out[f.name] = {
                "dimmer": max(0.12 + 0.35 * clamp01(c.level), 0.7 * accent, mover_flash),
                "pan": pan_lo + pan * (pan_hi - pan_lo),
                "tilt": tilt_lo + tilt * (tilt_hi - tilt_lo),
                "speed": 1.0,
                "color": PALETTE[(bar + i) % len(PALETTE)],
                "gobo": pick_gobo(cfg, (c.bar_no // 4) if self._bar_known else int(pos // 32), i),
                "strobe_hz": cfg.mover_flash_hz if burst else 0.0,
            }
        # wall wash: a calm colour per bar (opposite the mover's), bass-following floor, and a
        # chase that steps one segment per flash event (the whole bar on the ONE)
        seg = [d.step(c.dt) for d in self._seg]
        floor = 0.1 + 0.3 * clamp01(c.level)
        for f in self.washes:
            out[f.name] = {"colour": PALETTE[(bar + 3) % len(PALETTE)],
                           **{f"seg{i}": max(floor, 0.9 * seg[i]) for i in range(4)}}
        # Derby: on while the bass is there (dark in breakdowns), colour per bar
        gate = cfg.drive_flash_gate if self.kick_driven else cfg.flash_gate
        self._derby_on = energy >= (gate - 0.1 if self._derby_on else gate)
        for f in self.effects:
            out[f.name] = {"on": 1.0 if self._derby_on else 0.0,
                           "colour": PALETTE[(bar + 2) % len(PALETTE)],
                           "motor": cfg.derby_motor_beat}
        return out


class AmbientScene:
    """No reliable beat: breathe with the music's loudness, slow wander, slow colour drift.
    Only a very strong bass hit (a drop the beat tracker has not locked onto yet) flashes."""

    name = "ambient"

    def __init__(self, patch: Patch, cfg: ShowConfig, limiter: FlashLimiter) -> None:
        self.movers = patch.group("movers")
        self.strobes = patch.group("strobes")
        self.washes = patch.group("washes")
        self.effects = patch.group("effects")
        self._derby_on = False
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
        base = 0.15 + 0.5 * clamp01(c.level)  # follows the music's loudness...

        def breath(i: int) -> float:
            """...and slowly breathes: 1 at the top, (1 - depth) at the bottom of each cycle."""
            cycle = 0.5 + 0.5 * math.cos(2 * math.pi * (c.t / cfg.breath_period_s + 0.12 * i))
            return 1.0 - cfg.breath_depth * (1.0 - cycle)

        for i, f in enumerate(self.movers):
            pan = 0.5 + 0.5 * math.sin(2 * math.pi * (c.t / 45.0 + 0.3 * i))
            tilt = 0.5 + 0.5 * math.sin(2 * math.pi * (c.t / 31.0 + 0.5 + 0.2 * i))
            out[f.name] = {
                "dimmer": base * breath(i),
                "pan": pan_lo + pan * (pan_hi - pan_lo),
                "tilt": tilt_lo + tilt * (tilt_hi - tilt_lo),
                "speed": 0.6,
                "color": PALETTE[(int(c.t // 20) + i) % len(PALETTE)],
                "gobo": pick_gobo(cfg, int(c.t // 45), i),
            }
        # wall wash: slow colour drift, brightness breathing as a wave along the bar
        for f in self.washes:
            wave = {f"seg{i}": 0.7 * base * (1.0 - cfg.breath_depth * (1.0 - (0.5 + 0.5 * math.cos(
                2 * math.pi * (c.t / cfg.breath_period_s - 0.25 * i))))) for i in range(4)}
            out[f.name] = {"colour": PALETTE[(int(c.t // 20) + 3) % len(PALETTE)], **wave}
        self._derby_on = c.level >= (0.1 if self._derby_on else 0.15)
        for f in self.effects:
            out[f.name] = {"on": 1.0 if self._derby_on else 0.0,
                           "colour": PALETTE[(int(c.t // 20) + 2) % len(PALETTE)],
                           "motor": cfg.derby_motor_ambient}
        return out


@dataclass
class _MoverState:
    pan: Slew = field(default_factory=lambda: Slew(0.8))
    tilt: Slew = field(default_factory=lambda: Slew(0.8))
    colour: str = ""
    colour_t: float = -1e9
    gobo: str = "open"
    gobo_t: float = -1e9
    rgb: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])  # wash colour (smoothed)


class Show:
    def __init__(self, patch: Patch, cfg: ShowConfig | None = None) -> None:
        self.patch = patch
        self.cfg = cfg or ShowConfig()
        cap = self.cfg.max_flash_hz
        self._limiter = FlashLimiter(cap)
        self._beat = BeatScene(patch, self.cfg, self._limiter)
        self._ambient = AmbientScene(patch, self.cfg, self._limiter)
        self._drive = BeatScene(patch, self.cfg, self._limiter, kick_driven=True)
        self._scenes = {"beat": self._beat, "ambient": self._ambient, "drive": self._drive}
        self.mood = "ambient"  # the scene we are heading to
        self._from = "ambient"
        self._mix = 1.0  # 0 = fully `_from`, 1 = fully `mood`
        self._mood_since = 0.0
        self._lock_run = 0.0  # seconds the lock state has been stable
        self._last_locked = False
        self.override = self.cfg.scene if self.cfg.scene in self._scenes else "auto"
        self._kick_rate = 0.0
        self._drop_ef: float | None = None
        self._drop_ref = 0.0
        self._drop_hist: deque[float] = deque(maxlen=900)  # ~30 s at 30 Hz
        self._drop_n = 0
        self._groove_t = -1e9  # last time the music had a locked beat or was kick-dense
        self._drop_run = 0.0
        self._drop_high = 0.0
        self._last_drop = -1e9
        self._blast_t0: float | None = None
        self._force_white = False
        self._bar_known_until = -1.0  # the bar position was trusted at the last fired beat
        self._bar_no = 0
        self._blast_style = "flash"  # "flash" (white blast) or "blackout" (dark, then a pop)
        self._last_blackout_t = -1e9
        self._was_dark = False
        self.events: deque[tuple[float, str]] = deque(maxlen=8)  # (show time, message) for the UI
        self.drops = 0
        self.dark_drops = 0  # drops that went dark (blackout style)
        self.last_beat_t = self.last_kick_t = self.last_flash_t = -1e9
        self._flash_total = 0
        self._bass_avg = 0.0
        self._energetic_run = 0.0
        self._energetic_last = False
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
        self.mechanics = self.cfg.mechanics

    @property
    def flashes(self) -> int:
        return self._beat.flash_count + self._ambient.flash_count + self._drive.flash_count

    # -- controls -------------------------------------------------------------------
    def log_event(self, message: str) -> None:
        self.events.append((self.t, message))

    def command(self, key: str) -> str:
        """Handle a hotkey; returns a short status message."""
        msg = self._command(key)
        if msg:
            self.log_event(msg)
        return msg

    def _command(self, key: str) -> str:
        if key == "b":
            self.blackout = not self.blackout
            return "BLACKOUT" if self.blackout else "blackout off"
        if key == "s":
            self.strobe_enabled = not self.strobe_enabled
            self._beat.strobe_enabled = self._ambient.strobe_enabled = self.strobe_enabled
            self._drive.strobe_enabled = self.strobe_enabled
            return f"strobe light {'on' if self.strobe_enabled else 'OFF'}"
        if key == "m":
            self.mechanics = MECHANICS[(MECHANICS.index(self.mechanics) + 1) % len(MECHANICS)]
            return f"mechanics: {self.mechanics}"
        if key == "a":
            self.override = "auto"
            return "scene: auto"
        if key in ("1", "2", "3"):
            self.override = {"1": "beat", "2": "ambient", "3": "drive"}[key]
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
        if ctx.beats_fired:
            self.last_beat_t = self.t
        if ctx.kicks:
            self.last_kick_t = self.t
        if self.flashes != self._flash_total:
            self._flash_total = self.flashes
            self.last_flash_t = self.t
        intents = self._blend(ctx, dt)
        if ctx.drop:
            cfg = self.cfg
            dark = (cfg.drop_blackout_every_s > 0 and self.strobe_enabled
                    and self.t - self._last_blackout_t >= cfg.drop_blackout_every_s
                    and any(f.group == "movers" for f in self.patch.fixtures))
            self._blast_style = "blackout" if dark else "flash"
            if dark:
                self._last_blackout_t = self.t
                self.dark_drops += 1
            self._blast_t0, self._force_white = self.t, True
            self.drops += 1
            self.log_event("DROP -> blackout + strobe, then pop" if dark else "DROP -> blast")
            log.info("drop -> %s", self._blast_style)
        self._overlay_blast(intents)
        self._update_master(silent, dt)
        self._apply(intents, dt)

    def _context(self, f: Features | None, age_s: float, dt: float, silent: bool,
                 stale: bool) -> Ctx:
        phase, fired, kicks, locked, strength, bass, rise = 0.0, 0, 0, False, 0.0, 0.0, 0.0
        fired_bar = -1
        drop = False
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
                        if f.bar_beat >= 0 and f.bar_confidence >= self.cfg.bar_min_confidence:
                            ahead = math.floor(f.beat_phase + age_s * f.bpm / 60.0 + lead)
                            fired_bar = (f.bar_beat + max(ahead, 0)) % 4
                            self._bar_known_until = self.t + 2.0
                            if fired_bar == 0:
                                self._bar_no += 1
                self._prev_adj = adj
            kicks = f.kick_count - self._last_kick_count
            self._last_kick_count = f.kick_count
            strength = f.last_kick_strength
            rise = f.last_kick_rise_db
            bass = f.bass_energy
            drop = self._detect_drop(bass, dt)
            self._level = smooth_toward(self._level, f.level, dt, 0.6)
        inst = kicks / dt if dt > 0 else 0.0
        self._kick_rate = smooth_toward(self._kick_rate, inst, dt, 2.0)
        self._bass_avg = smooth_toward(self._bass_avg, bass, dt, 1.0)
        energetic = (not silent and f is not None and self._kick_rate >= self.cfg.drive_kick_rate
                     and self._bass_avg >= self.cfg.drive_energy
                     and f.beat_confidence >= self.cfg.drive_min_confidence)
        if locked or energetic:
            self._groove_t = self.t
        # Beat position integrates smoothly (it paces the sweeps); a fired grid beat only
        # nudges it toward the nearest whole beat, so grid re-syncs never make the head jump.
        if self._bpm > 0:
            self._beat_pos += dt * self._bpm / 60.0
            if fired:
                self._beat_pos += 0.25 * (round(self._beat_pos) - self._beat_pos)
        return Ctx(self.t, dt, f, silent, locked, phase, fired, self._beat_pos, self._level,
                   bass, max(0, kicks), strength, rise, bass, energetic, drop,
                   fired_bar if fired else -1, self.t < self._bar_known_until, self._bar_no)

    def _detect_drop(self, bass: float, dt: float) -> bool:
        """True once when the bass comes back after a breakdown.

        Relative to the section's own loudness: the reference is the 85th percentile of the
        smoothed bass energy over the last 30 s (ignores start-up spikes, and a breakdown of up
        to ~25 s cannot erase it). A breakdown is the smoothed energy sitting `drop_dip` or more
        below the reference; the drop is a sustained return to within 0.12 of it.
        """
        cfg = self.cfg
        self._drop_ef = bass if self._drop_ef is None else smooth_toward(
            self._drop_ef, bass, dt, 0.6)
        self._drop_hist.append(self._drop_ef)
        self._drop_n += 1
        if self._drop_n % 5 == 0 and len(self._drop_hist) >= 150:  # >= 5 s of history
            self._drop_ref = float(np.percentile(self._drop_hist, 85))
        if len(self._drop_hist) < 150 or self.t - self._groove_t > 30.0:
            return False  # no drops in music that has no groove (ballads, pads, speech)
        gap = self._drop_ref - self._drop_ef
        if gap >= cfg.drop_dip:
            self._drop_run += dt
            self._drop_high = 0.0
        elif gap <= 0.12:
            self._drop_high += dt
            if self._drop_high >= 0.4:
                drop = (self._drop_run >= 1.0 and self.t - self._last_drop >= cfg.drop_min_gap_s)
                self._drop_run = 0.0
                if drop:
                    self._last_drop = self.t
                return drop
        else:
            self._drop_high = 0.0
        return False

    def _blast_levels(self) -> tuple[float, float, bool] | None:
        """(strobe level, mover level, mover forced dark) of the drop effect, or None.

        "flash" style: the strobe light stutters at 6 Hz (under the flash cap) for the first
        40 % of `drop_duration_s` and then fades; the moving head jumps to full white, holds
        through the stutter part and fades with it.
        "blackout" style: for `drop_blackout_s` the moving head is forced DARK while the strobe
        stutters in true darkness (6 Hz, off = 0), then the head pops back to full white for
        0.4 s and fades over 1 s."""
        if self._blast_t0 is None:
            return None
        rel = self.t - self._blast_t0
        if self._blast_style == "blackout":
            dark = self.cfg.drop_blackout_s
            if rel >= dark + 1.4:
                self._blast_t0 = None
                return None
            if rel < dark:
                return (1.0 if (rel * 6.0) % 1.0 < 0.55 else 0.0), 0.0, True
            if rel < dark + 0.4:
                return 0.0, 1.0, False
            return 0.0, 1.0 - (rel - dark - 0.4), False
        dur = self.cfg.drop_duration_s
        if rel >= dur:
            self._blast_t0 = None
            return None
        stutter = 0.4 * dur
        fade = 1.0 - (rel - stutter) / (dur - stutter)
        if rel < stutter:
            return (1.0 if (rel * 6.0) % 1.0 < 0.55 else 0.2), 1.0, False
        return fade, fade, False

    def _update_mood(self, c: Ctx, dt: float) -> None:
        if self.override != "auto":
            target = self.override
        else:
            if c.locked == self._last_locked:
                self._lock_run += dt
            else:
                self._lock_run, self._last_locked = 0.0, c.locked
            if c.energetic == self._energetic_last:
                self._energetic_run += dt
            else:
                self._energetic_run, self._energetic_last = 0.0, c.energetic
            held = self.t - self._mood_since
            target = self.mood
            if self.mood == "beat":
                # the grid survives breakdowns: leave only after a long loss of lock
                if not c.locked and self._lock_run >= 10.0 and held >= 6.0:
                    target = "drive" if c.energetic else "ambient"
            else:
                if c.locked and self._lock_run >= 1.5 and held >= 3.0:
                    target = "beat"
                elif self.mood == "ambient":
                    if c.energetic and self._energetic_run >= 1.0 and held >= 2.0:
                        target = "drive"
                elif not c.energetic and self._energetic_run >= 4.0 and held >= 4.0:
                    target = "ambient"  # drive -> ambient once the energy has been gone a while
        if target != self.mood:
            self._from, self.mood = self.mood, target
            self._mix, self._mood_since = 0.0, self.t
            log.info("scene -> %s", target)
            self.log_event(f"scene -> {target}")
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

    def _overlay_blast(self, intents: Intent) -> None:
        levels = self._blast_levels()
        if levels is None:
            return
        strobe_level, mover_level, forced_dark = levels
        if self._was_dark and not forced_dark:
            self._force_white = True  # the pop at the end of a dark drop is white
        self._was_dark = forced_dark
        for fx in self.patch.fixtures:
            want = intents.setdefault(fx.name, {})
            if fx.group == "strobes" and self.strobe_enabled:
                scene = float(want.get("dimmer", 0.0))
                # blackout style: a clean stutter in the dark, no scene flashes mixed in
                want["dimmer"] = strobe_level if self._blast_style == "blackout" and (
                    forced_dark) else max(scene, strobe_level)
            elif fx.group == "washes":
                if forced_dark:
                    for i in range(4):
                        want[f"seg{i}"] = 0.0
                else:
                    want["colour"] = "white"
                    for i in range(4):
                        want[f"seg{i}"] = max(float(want.get(f"seg{i}", 0.0)), mover_level)
            elif fx.group == "effects":
                if forced_dark:
                    want["on"] = 0.0
                else:
                    want.update({"on": 1.0, "colour": "white", "white": 1.0})
            elif fx.group == "movers":
                if forced_dark:
                    want["dimmer"] = 0.0
                    want["strobe_hz"] = 0.0  # no mover flash bursts either
                else:
                    want["dimmer"] = max(float(want.get("dimmer", 0.0)), mover_level)

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
            if fx.group == "washes":
                self._apply_wash(fx, st, want, dt)
                continue
            if fx.group == "effects":
                if self.mechanics != "full":
                    want = {**want, "motor": "hold", **({"on": 0.0} if self.mechanics == "off"
                                                        else {})}
                self._apply_effect(fx, st, want)
                continue
            if fx.group == "movers" and self.mechanics != "full":
                want = self._wear_free(fx, st, want)
            if "dimmer" in want and fx.has("dimmer"):
                level = 0.0 if self.blackout else clamp01(float(want["dimmer"])) * self._master_now
                fx.set("dimmer", level)
            if fx.has("strobe"):
                hz = float(want.get("strobe_hz", 0.0))  # only mover flash bursts use it
                if hz > 0 and self.strobe_enabled and not self.blackout and fx.group == "movers":
                    fx.set_hz("strobe", hz)  # capped by the profile's safe_max_hz
                else:
                    fx.set("strobe", 0.0)  # otherwise the built-in strobe stays off
            if "pan" in want and fx.has("pan"):
                pan = _clip(float(want["pan"]), pan_lo, pan_hi)
                fx.set("pan", clamp01(st.pan.step(pan, dt)))
            if "tilt" in want and fx.has("tilt"):
                tilt = _clip(float(want["tilt"]), tilt_lo, tilt_hi)
                fx.set("tilt", clamp01(st.tilt.step(tilt, dt)))
            if "speed" in want and fx.has("speed"):
                fx.set("speed", clamp01(float(want["speed"])))
            if self._force_white and fx.has("color") and fx.group == "movers" and (
                    self.mechanics == "full"):
                st.colour, st.colour_t = "white", self.t  # the blast is white, whatever the hold
            if "gobo" in want and fx.has("gobo"):
                gobo = str(want["gobo"])
                if self._blast_t0 is not None and fx.group == "movers":
                    gobo = "open"  # the drop effect is a clean white beam
                if gobo != st.gobo and (self.t - st.gobo_t >= self.cfg.gobo_hold_s
                                        or gobo == "open" and self._blast_t0 is not None):
                    st.gobo, st.gobo_t = gobo, self.t
                fx.set("gobo", st.gobo)
            if "color" in want and fx.has("color"):
                colour = str(want["color"])
                if colour != st.colour and self.t - st.colour_t >= self.cfg.colour_hold_s:
                    st.colour, st.colour_t = colour, self.t
                if st.colour:
                    fx.set("color", st.colour)
        self._force_white = False

    def _wear_free(self, fx, st: _MoverState, want: dict) -> dict:
        """Moving head in static/off mode: parked, gobo open, colour fixed; dark when off.
        The wheels are set directly (no hold time): this is one move at the mode change, not
        the constant shuffling the hold times protect against."""
        cfg = self.cfg
        want = {**want, "pan": cfg.park_pan, "tilt": cfg.park_tilt, "gobo": "open",
                "color": cfg.static_colour}
        st.gobo, st.gobo_t = "open", self.t
        st.colour, st.colour_t = cfg.static_colour, self.t
        if self.mechanics == "off":
            want["dimmer"] = 0.0
            want["strobe_hz"] = 0.0
        return want

    def _apply_wash(self, fx, st: _MoverState, want: dict, dt: float) -> None:
        """RGB bar: colour crossfades (snaps during a drop blast), brightness is immediate."""
        name = str(want.get("colour", "white"))
        target = dict(self.cfg.wash_colours).get(name) or WASH_RGB.get(name, WASH_RGB["white"])
        tau = 0.02 if self._blast_t0 is not None else 0.25
        st.rgb = [smooth_toward(a, b, dt, tau) for a, b in zip(st.rgb, target, strict=True)]
        for i in range(4):
            level = 0.0 if self.blackout else clamp01(float(want.get(f"seg{i}", 0.0)))
            level = level ** self.cfg.wash_gamma * self.cfg.wash_gain * self._master_now
            for ch, v, bal in zip("rgb", st.rgb, self.cfg.wash_balance, strict=True):
                fx.set(f"s{i + 1}_{ch}", clamp01(v * bal * level))

    def _apply_effect(self, fx, st: _MoverState, want: dict) -> None:
        """Derby: on/off only (no dimmer): colour slot with a hold time, beam motor, white LEDs.
        The colour-strobe channel is a fixed 0 in the profile and the white strobe is unused."""
        on = (float(want.get("on", 0.0)) > 0.5 and not self.blackout
              and self._master_now > 0.05)
        if not on:
            fx.set("color", "off")
            fx.set("motor", "hold")
            fx.set("white", "off")
            return
        colour = str(want.get("colour", "white"))
        if colour != st.colour and (self.t - st.colour_t >= self.cfg.colour_hold_s
                                    or colour == "white" and self._blast_t0 is not None):
            st.colour, st.colour_t = colour, self.t
        fx.set("color", DERBY_COLOUR.get(st.colour or colour, "white"))
        fx.set("motor", str(want.get("motor", "slow")))
        fx.set("white", "full" if want.get("white") else "off")


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
