import time
from pathlib import Path

import numpy as np
import pytest

from dmxreactive.analysis.analyzer import Analyzer
from dmxreactive.audio.array_source import click_track
from dmxreactive.config import load_config
from dmxreactive.engine.show import Show, ShowConfig
from dmxreactive.engine.sim import simulate

SR, BLOCK = 48000, 1024
EXAMPLE = Path(__file__).parent.parent / "config" / "config.example.yaml"


def analyse(x):
    an = Analyzer(SR, BLOCK)
    return [an.process(x[i:i + BLOCK]) for i in range(0, len(x) - BLOCK + 1, BLOCK)]


@pytest.fixture(scope="module")
def clicks150():
    return analyse(click_track(150, 40.0, SR))


def new_show(**kw):
    cfg = load_config(EXAMPLE)
    return Show(cfg.patch, ShowConfig(**kw)), cfg.patch


def strobe_series(ticks):
    return np.array([tk.dmx["strobe1"][0] for tk in ticks])  # strobe fixture dimmer


def mover(ticks, idx):
    return np.array([tk.dmx["mover1"][idx] for tk in ticks])


def flash_times(series, ticks, thresh=100):
    rising = [i for i in range(1, len(series)) if series[i] >= thresh > series[i - 1]]
    return np.array([ticks[i].t for i in rising])


def test_starts_in_ambient_then_switches_to_beat_when_locked(clicks150):
    show, _ = new_show()
    ticks = simulate(show, clicks150)
    assert ticks[0].mood == "ambient"
    first_beat = next(tk.t for tk in ticks if tk.mood == "beat")
    assert 6.0 <= first_beat <= 30.0  # needs lock + dwell time, but not forever
    assert ticks[-1].mood == "beat"


def test_flashes_follow_the_grid_with_lead(clicks150):
    show, _ = new_show()
    ticks = simulate(show, clicks150)
    late = [tk for tk in ticks if tk.t > 25]
    flashes = flash_times(strobe_series(late), late)
    true = np.arange(0, 40, 60 / 150)
    off = np.array([(f - true[np.argmin(abs(true - f))]) * 1000 for f in flashes])
    assert abs(len(flashes) - 15 * 2.5) <= 3  # 15 s at 150 BPM
    assert np.median(np.abs(off)) < 45  # within about one render frame of the real click


def test_flash_rate_never_exceeds_the_cap():
    # a very fast click track tries to flash faster than allowed
    feats = analyse(click_track(190, 40.0, SR))
    show, _ = new_show(max_flash_hz=2.0, scene="beat")
    ticks = simulate(show, feats)
    flashes = flash_times(strobe_series(ticks), ticks)
    assert len(flashes) > 10
    assert np.min(np.diff(flashes)) >= 0.5 - 1 / 30 - 1e-6


def test_no_beat_scene_and_bounded_flashing_without_a_locked_beat():
    rng = np.random.default_rng(5)
    x = 0.2 * rng.standard_normal(SR * 30).astype(np.float32)  # noise: no beat
    ticks = simulate(new_show()[0], analyse(x))
    assert all(tk.mood == "ambient" for tk in ticks)
    # ambient may flash on very strong bass hits, but never faster than ambient_flash_hz
    flashes = flash_times(strobe_series(ticks), ticks, thresh=60)
    assert len(flashes) <= 30 * 3 * 0.7
    if len(flashes) > 1:
        assert np.min(np.diff(flashes)) >= 1 / 3 - 2 / 30


def test_silence_fades_everything_to_dark(clicks150):
    x = np.concatenate([click_track(150, 25.0, SR), np.zeros(SR * 8, dtype=np.float32)])
    ticks = simulate(new_show()[0], analyse(x))
    assert mover(ticks, 7).max() > 100
    tail = [tk for tk in ticks if tk.t > 31]
    assert mover(tail, 7).max() == 0 and strobe_series(tail).max() == 0


def test_no_audio_at_all_is_dark_not_a_crash():
    show, patch = new_show()
    for _ in range(100):
        show.step(None, 0.0, 1 / 30)
    assert all(f.dmx_values()[f.profile.channels["dimmer"].offset] == 0 for f in patch.fixtures)


def test_blackout_key_is_immediate_and_reversible(clicks150):
    show, patch = new_show(scene="beat")
    seen = {}

    def hook(i, t, s):
        if i == 600:
            s.command("b")
        if i == 700:
            s.command("b")
        if i in (601, 699):
            seen[i] = [f.dmx_values() for f in patch.fixtures]

    ticks = simulate(show, clicks150, on_tick=hook)
    assert mover(ticks[601:700], 7).max() == 0 and strobe_series(ticks[601:700]).max() == 0
    assert mover(ticks[701:], 7).max() > 0


def test_strobe_key_disables_flashes_only(clicks150):
    show, _ = new_show(scene="beat")
    ticks = simulate(show, clicks150, on_tick=lambda i, t, s: s.command("s") if i == 450 else None)
    assert strobe_series(ticks[:440]).max() > 100
    assert strobe_series(ticks[455:]).max() == 0
    assert mover(ticks[455:], 7).max() > 0  # the mover keeps working


def test_movement_respects_limits_and_motor_rate(clicks150):
    show, _ = new_show(pan_limits=(0.3, 0.7), tilt_limits=(0.4, 0.6))
    ticks = simulate(show, clicks150)
    pan = (mover(ticks, 0).astype(int) * 256 + mover(ticks, 2)) / 65535
    tilt = (mover(ticks, 1).astype(int) * 256 + mover(ticks, 3)) / 65535
    assert pan.min() >= 0.3 - 0.01 and pan.max() <= 0.7 + 0.01
    assert tilt.min() >= 0.4 - 0.01 and tilt.max() <= 0.6 + 0.01
    assert np.max(np.abs(np.diff(pan))) * 30 <= 0.85  # slew limit 0.8/s


def test_colour_changes_are_rate_limited(clicks150):
    show, _ = new_show(scene="beat", colour_hold_s=2.0)
    ticks = simulate(show, clicks150)
    colour = mover(ticks, 5)
    changes = [ticks[i].t for i in range(1, len(colour)) if colour[i] != colour[i - 1]]
    assert len(changes) >= 3
    assert np.min(np.diff(changes)) >= 2.0 - 1 / 30 - 1e-6


def test_crossfade_has_no_jump_in_brightness(clicks150):
    show, _ = new_show()
    ticks = simulate(show, clicks150)
    d = mover(ticks, 7).astype(int)
    switch = next(i for i, tk in enumerate(ticks) if tk.mood == "beat")
    window = d[switch - 3:switch + 40]
    # beat-accent pulses are allowed, but the scene change itself must not slam to full brightness
    assert abs(int(d[switch]) - int(d[switch - 1])) < 60
    assert window.size > 0


def test_forced_scene_overrides_auto(clicks150):
    show, _ = new_show(scene="ambient")
    ticks = simulate(show, clicks150)
    assert all(tk.mood == "ambient" for tk in ticks)
    flashes = flash_times(strobe_series(ticks), ticks, thresh=60)
    if len(flashes) > 1:
        assert np.min(np.diff(flashes)) >= 1 / 3 - 2 / 30  # ambient flash cap


def test_show_step_is_cheap(clicks150):
    show, _ = new_show()
    t0 = time.perf_counter()
    simulate(show, clicks150)
    ticks = int(clicks150[-1].t * 30)
    assert (time.perf_counter() - t0) / ticks < 0.001  # < 1 ms per 33 ms tick


def test_breakdown_stays_dark_but_the_drop_flashes_immediately():
    """Kicks, then 8 s of non-percussive pad (grid keeps running), then kicks again."""
    sr = SR
    pad = 0.15 * sine_pad(8.0)
    x = np.concatenate([click_track(150, 24.0, sr), pad, click_track(150, 12.0, sr)])
    show, _ = new_show()
    ticks = simulate(show, analyse(x))
    series = strobe_series(ticks)
    hits = flash_times(series, ticks)
    in_breakdown = hits[(hits > 26.0) & (hits < 31.5)]
    after_drop = hits[(hits > 32.2) & (hits < 36.0)]
    assert len(in_breakdown) == 0  # no flashing in the bass-less section
    assert len(after_drop) >= 4  # flashes resume within a second of the kicks returning


def sine_pad(seconds):
    t = np.arange(int(seconds * SR)) / SR
    return (np.sin(2 * np.pi * 880 * t) + np.sin(2 * np.pi * 1320 * t)).astype(np.float32) * 0.5
