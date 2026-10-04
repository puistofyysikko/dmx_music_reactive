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


def test_noise_never_gets_the_beat_scene_and_flashing_stays_under_the_cap():
    rng = np.random.default_rng(5)
    x = 0.2 * rng.standard_normal(SR * 30).astype(np.float32)  # noise: no beat
    ticks = simulate(new_show()[0], analyse(x))
    assert all(tk.mood != "beat" for tk in ticks)
    # loud broadband noise can look kick-dense (drive scene), but the flash cap always holds
    flashes = flash_times(strobe_series(ticks), ticks, thresh=60)
    if len(flashes) > 1:
        assert np.min(np.diff(flashes)) >= 1 / 8 - 1 / 30 - 1e-6


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


def test_drive_scene_flashes_on_dense_irregular_kicks():
    """Frenchcore-like: kicks 4/s with irregular spacing, so no tempo grid can lock."""
    rng = np.random.default_rng(2)
    x = np.zeros(SR * 40, dtype=np.float32)
    click = click_track(60, 1.0, SR)[: int(0.05 * SR)]
    t = 0.5
    while t < 38:
        i = int(t * SR)
        x[i:i + len(click)] += click
        t += rng.choice([0.17, 0.29, 0.29, 0.24])
    feats = analyse(x)
    show, _ = new_show()
    ticks = simulate(show, feats)
    moods = [tk.mood for tk in ticks]
    assert moods.count("drive") > 0.6 * len(moods)
    flashes = flash_times(strobe_series(ticks), ticks)
    assert len(flashes) > 0.6 * feats[-1].kick_count  # most kicks flash (the cap drops some)
    assert np.min(np.diff(flashes)) >= 1 / 8 - 1 / 30 - 1e-6  # never above the hard cap


def test_drive_scene_is_not_used_for_a_calm_ballad_pulse():
    feats = analyse(click_track(60, 40.0, SR))  # one kick per second
    ticks = simulate(new_show()[0], feats)
    assert all(tk.mood != "drive" for tk in ticks)


def test_forcing_drive_with_key_3():
    show, _ = new_show()
    assert show.command("3") == "scene: drive (forced)"
    ticks = simulate(show, analyse(click_track(120, 15.0, SR)))
    assert all(tk.mood == "drive" for tk in ticks[60:])


def steady_tone_feats(seconds=45):
    return analyse(0.3 * sine_pad(seconds))


def test_ambient_mover_breathes_slowly_and_can_be_switched_off():
    feats = steady_tone_feats(45)
    breathing = mover(simulate(new_show(scene="ambient")[0], feats), 7)[150:].astype(float)
    flat = mover(simulate(new_show(scene="ambient", breath_depth=0.0)[0], feats), 7)[150:]
    flat = flat.astype(float)
    assert breathing.max() / max(breathing.min(), 1) > 2.0  # clear dips and swells
    assert flat.max() / max(flat.min(), 1) < 1.15  # depth 0: steady
    spectrum = np.abs(np.fft.rfft(breathing - breathing.mean()))
    freqs = np.fft.rfftfreq(len(breathing), 1 / 30)
    assert abs(1 / freqs[int(np.argmax(spectrum))] - 7.0) < 1.0  # breathing period ~ 7 s


def test_mover_flashes_on_every_fourth_flash_event_in_dimmer_mode(clicks150):
    show, _ = new_show(scene="beat", mover_flash_mode="dimmer")
    ticks = simulate(show, clicks150)
    late = ticks[600:]
    full = 0.8 * 255 * 0.9  # master 0.8: a real flash reaches ~full master brightness
    mover_peaks = flash_times(mover(late, 7).astype(float), late, thresh=full)
    strobe_flashes = flash_times(strobe_series(late), late)
    ratio = len(mover_peaks) / max(len(strobe_flashes), 1)
    assert 0.15 < ratio < 0.4  # about every 4th


def test_mover_flash_can_be_disabled_and_obeys_the_strobe_key(clicks150):
    off, _ = new_show(scene="beat", mover_flash_every=0, mover_flash_mode="dimmer")
    ticks = simulate(off, clicks150)[600:]
    assert mover(ticks, 7).max() < 0.8 * 255 * 0.8  # pulses stay below a flash
    show, _ = new_show(scene="beat", mover_flash_mode="dimmer")
    show.command("s")
    ticks = simulate(show, clicks150)[600:]
    assert mover(ticks, 7).max() < 0.8 * 255 * 0.8 and strobe_series(ticks).max() == 0


def drop_scenario_feats():
    pad = 0.15 * sine_pad(8.0)
    return analyse(np.concatenate([click_track(150, 24.0, SR), pad, click_track(150, 12.0, SR)]))


def test_drop_blast_lasts_about_a_second_and_a_half_and_is_white():
    show, patch = new_show(drop_duration_s=1.5, drop_blackout_every_s=0)
    ticks = simulate(show, drop_scenario_feats())
    mv = mover(ticks, 7).astype(float)
    # blast start: first tick after the pad where the mover jumps to ~full master brightness
    assert show._last_drop > 31.0  # detected when the kicks come back after the pad
    start = round(show._last_drop * 30)
    # the mover stays bright through the blast (hold, then fade): look 0.5 s after the start
    assert mv[start + 15] > 0.6 * 255 * 0.8  # still bright at 0.5 s
    stutter = strobe_series(ticks[start:start + 18])  # strobe stutter in the first 0.6 s
    assert stutter.max() > 0.4 * 255
    assert ticks[start].dmx["mover1"][5] == 5  # colour wheel: white
    # after the blast: ordinary beat pulses again (peak ~0.7 of master, not held at full)
    assert mv[start + 75:start + 105].min() < 0.5 * 255


def test_drop_blast_stutter_respects_the_flash_cap():
    show, _ = new_show(drop_duration_s=2.0, max_flash_hz=8.0, drop_blackout_every_s=0)
    ticks = simulate(show, drop_scenario_feats())
    sv = strobe_series(ticks)
    window = [(tk.t, v) for tk, v in zip(ticks, sv, strict=False) if 31.5 < tk.t < 34.5]
    rises = [window[i][0] for i in range(1, len(window))
             if window[i][1] >= 0.45 * 255 > window[i - 1][1]]
    assert len(rises) >= 3
    assert np.min(np.diff(rises)) >= 1 / 8 - 1 / 30 - 1e-6


def test_drop_is_detected_on_a_real_breakdown_but_not_in_steady_music(clicks150):
    show, _ = new_show()
    simulate(show, drop_scenario_feats())
    assert show._last_drop > 0  # fired in the breakdown scenario
    steady, _ = new_show()
    simulate(steady, clicks150)
    assert steady._last_drop < 0  # steady 4/4 music never triggers a blast


def test_blast_obeys_strobe_key_for_the_strobe_light_only():
    show, _ = new_show()
    show.command("s")
    ticks = simulate(show, drop_scenario_feats())
    late = [tk for tk in ticks if 31.5 < tk.t < 35]
    assert strobe_series(late).max() == 0  # flashing off means off
    assert mover(late, 7).max() >= 0.8 * 255 * 0.95  # the mover swell still happens


def test_hold_decay_envelope_holds_then_decays():
    from dmxreactive.engine.primitives import HoldDecay
    env = HoldDecay(0.2, 0.1)
    env.trigger(1.0)
    values = [env.step(1 / 30) for _ in range(15)]
    assert values[0] == 1.0 and all(v == 1.0 for v in values[:6])  # 200 ms hold = 6 frames
    assert values[7] < 1.0 and values[-1] < values[7]


def test_mover_strobe_bursts_every_fourth_event_with_the_cap_respected(clicks150):
    show, _ = new_show(scene="beat")  # default mover_flash_mode = strobe
    ticks = simulate(show, clicks150)[600:]
    burst = mover(ticks, 8)  # the head's built-in strobe channel
    starts = [i for i in range(1, len(burst)) if burst[i] > 0 >= burst[i - 1]]
    strobe_flashes = flash_times(strobe_series(ticks), ticks)
    assert 0.15 < len(starts) / max(len(strobe_flashes), 1) < 0.4  # about every 4th
    lengths = []
    for i in starts:
        j = i
        while j < len(burst) and burst[j] > 0:
            j += 1
        lengths.append((j - i) / 30)
    assert 0.2 <= np.median(lengths) <= 0.32  # ~250 ms bursts
    assert burst.max() <= 101  # PicoSpot cap: 10 Hz = DMX 101
    assert burst.max() >= 70  # ... and an 8 Hz burst is really commanded


def test_mover_strobe_bursts_off_with_the_key_the_setting_and_blackout(clicks150):
    keyed, _ = new_show(scene="beat")
    keyed.command("s")
    assert mover(simulate(keyed, clicks150)[600:], 8).max() == 0
    never, _ = new_show(scene="beat", mover_flash_every=0)
    assert mover(simulate(never, clicks150)[600:], 8).max() == 0
    dark, _ = new_show(scene="beat")
    dark.command("b")
    ticks = simulate(dark, clicks150)[600:]
    assert mover(ticks, 8).max() == 0 and mover(ticks, 7).max() == 0


def test_negative_lead_delays_the_predicted_beat_flashes(clicks150):
    def flashes_for(lead):
        show, _ = new_show(scene="beat", beat_lead_ms=lead, kick_flash_strength=2.0, flash_gate=0.0)
        ticks = simulate(show, clicks150)
        late = [tk for tk in ticks if tk.t > 20]
        return flash_times(strobe_series(late), late)

    base, delayed = flashes_for(0.0), flashes_for(-30.0)
    n = min(len(base), len(delayed))
    shift = np.median((delayed[:n] - base[:n]) % 0.4) * 1000
    assert 15 <= shift <= 50  # about +30 ms (one render frame of 33 ms quantisation)


def test_a_drop_can_go_dark_then_pop():
    show, _ = new_show(drop_blackout_every_s=1.0, drop_blackout_s=1.5)
    ticks = simulate(show, drop_scenario_feats())
    assert show.dark_drops == 1 and show.drops == 1
    i0 = round(show._last_drop * 30)
    dark = ticks[i0 + 2:i0 + 42]  # inside the 1.5 s dark part (skip the 2-tick fade-in)
    assert mover(dark, 7).max() == 0 and mover(dark, 8).max() == 0  # head fully dark, no bursts
    stutter = strobe_series(dark)
    assert stutter.max() > 0.45 * 255 and (stutter == 0).sum() >= 8  # flashing in true darkness
    pop = ticks[i0 + 46:i0 + 60]  # 1.5 s .. 2.0 s after the drop
    assert mover(pop, 7).max() >= 0.8 * 255 * 0.95
    assert pop[0].dmx["mover1"][5] == 5  # white
    assert mover(ticks[i0 + 110:i0 + 125], 7).max() < 0.8 * 255 * 0.95  # blast over, normal again


def test_dark_drops_are_rationed_and_other_drops_are_white_blasts():
    pad = 0.15 * sine_pad(8.0)
    x = np.concatenate([click_track(150, 24.0, SR), pad, click_track(150, 20.0, SR), pad,
                        click_track(150, 12.0, SR)])
    show, _ = new_show(drop_blackout_every_s=300.0)
    simulate(show, analyse(x))
    assert show.drops >= 2 and show.dark_drops == 1  # only the first one may go dark


def test_no_dark_drop_when_flashing_is_off_or_the_feature_is_disabled():
    for kwargs, key in (({"drop_blackout_every_s": 0.0}, None),
                        ({"drop_blackout_every_s": 5.0}, "s")):
        show, _ = new_show(**kwargs)
        if key:
            show.command(key)
        simulate(show, drop_scenario_feats())
        assert show.drops >= 1 and show.dark_drops == 0


def test_the_one_gets_the_hardest_flash_and_the_mover_flash(clicks150):
    from test_analysis import bar_track  # synthetic 4/4: bass note + crash on every bar's ONE

    x, ones = bar_track(150.0, 70.0)
    feats = analyse(x)
    show, _ = new_show(scene="beat", kick_flash_strength=2.0, flash_gate=0.0, beat_lead_ms=0.0)
    ticks = simulate(show, feats)
    late = [tk for tk in ticks if tk.t > 28]
    # mover flashes (strobe-channel bursts) start near the bar's ONE, not on arbitrary beats
    burst = mover(late, 8)
    starts = [late[i].t for i in range(1, len(late)) if burst[i] > 0 >= burst[i - 1]]
    assert len(starts) >= 6
    err = np.array([np.min(np.abs(ones - t)) for t in starts])
    assert np.mean(err < 0.12) >= 0.8
    # the strobe light flashes harder on the ones than on the other beats
    series = strobe_series(late)
    peaks = [(late[i].t, series[i]) for i in range(1, len(late) - 1)
             if series[i] >= series[i - 1] and series[i] > series[i + 1] and series[i] > 40]
    on_one = [v for t, v in peaks if np.min(np.abs(ones - t)) < 0.12]
    off_one = [v for t, v in peaks if np.min(np.abs(ones - t)) >= 0.12]
    assert len(on_one) >= 5 and len(off_one) >= 10
    assert np.median(on_one) > 1.15 * np.median(off_one)


def test_without_a_trusted_bar_the_old_every_nth_behaviour_remains(clicks150):
    show, _ = new_show(scene="beat", bar_min_confidence=2.0)  # never trusted
    ticks = simulate(show, clicks150)[600:]
    burst = mover(ticks, 8)
    starts = [i for i in range(1, len(burst)) if burst[i] > 0 >= burst[i - 1]]
    strobe_flashes = flash_times(strobe_series(ticks), ticks)
    assert 0.15 < len(starts) / max(len(strobe_flashes), 1) < 0.4
