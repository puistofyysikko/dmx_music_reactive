"""Wall wash (Eurolite PIX-72, 12C2) and Derby (Varytec LED Derby ST, 4ch): profiles and roles."""

from pathlib import Path

import numpy as np
import pytest

from dmxreactive.analysis.analyzer import Analyzer
from dmxreactive.audio.array_source import click_track
from dmxreactive.config import load_config
from dmxreactive.dmx.profiles import FixtureProfile
from dmxreactive.engine.show import Show, ShowConfig
from dmxreactive.engine.sim import simulate

SR, BLOCK = 48000, 1024
ROOT = Path(__file__).parent.parent
EXAMPLE = ROOT / "config" / "config.example.yaml"


def analyse(x):
    an = Analyzer(SR, BLOCK)
    return [an.process(x[i:i + BLOCK]) for i in range(0, len(x) - BLOCK + 1, BLOCK)]


def new_show(**kw):
    cfg = load_config(EXAMPLE)
    return Show(cfg.patch, ShowConfig(**kw)), cfg.patch


def pad(seconds):
    t = np.arange(int(seconds * SR)) / SR
    wave = np.sin(2 * np.pi * 880 * t) + np.sin(2 * np.pi * 1320 * t)
    return (0.15 * 0.5 * wave).astype(np.float32)


def breakdown_feats():
    return analyse(np.concatenate([click_track(150, 24.0, SR), pad(8.0),
                                   click_track(150, 12.0, SR)]))


def wash(ticks, i):
    """Brightness series (0..255) of segment i (0..3): its brightest channel."""
    return np.array([max(tk.dmx["wash1"][3 * i:3 * i + 3]) for tk in ticks])


def derby(ticks, idx):
    return np.array([tk.dmx["derby1"][idx] for tk in ticks])


@pytest.fixture(scope="module")
def clicks150():
    return analyse(click_track(150, 40.0, SR))


# -- profiles ---------------------------------------------------------------------------
def test_pix72_profile_is_four_independent_rgb_segments():
    p = FixtureProfile.load(ROOT / "config/fixtures/pix72_12c2.json")
    assert p.channel_count == 12
    names = [c.name for c in sorted(p.channels.values(), key=lambda c: c.offset)]
    assert names == [f"s{s}_{c}" for s in range(1, 5) for c in "rgb"]  # manual p. 26, 12-2 mode


def test_derby_profile_keeps_both_strobe_channels_unused():
    p = FixtureProfile.load(ROOT / "config/fixtures/derbyst_4ch.json")
    assert p.channel_count == 4
    assert p.channels["color_strobe"].kind == "fixed" and p.channels["color_strobe"].value == 0
    assert p.channels["white"].slots == {"off": 0, "full": 200}  # 180-255 = no strobe pattern
    assert p.channels["color"].slots["white"] == 58 and p.channels["color"].slots["off"] == 0


def test_example_patch_has_no_overlaps_and_the_new_groups():
    _, patch = new_show()
    assert [f.name for f in patch.group("washes")] == ["wash1"]
    assert [f.name for f in patch.group("effects")] == ["derby1"]


# -- roles ------------------------------------------------------------------------------
def test_beat_scene_chases_the_segments_and_lights_the_derby(clicks150):
    show, _ = new_show()
    ticks = simulate(show, clicks150)
    late = [tk for tk in ticks if tk.mood == "beat" and tk.t > 20]
    assert late
    segs = [wash(late, i) for i in range(4)]
    assert all(s.max() > 100 for s in segs)  # every segment gets its turn (colour-independent)
    assert min(s.min() for s in segs) > 0  # the bass-following floor keeps the wall lit
    # a chase, not a flat pulse: between ONEs the segments take turns being the brightest
    assert sum(len({int(segs[i][k]) for i in range(4)}) > 1 for k in range(len(late))) > 50
    assert derby(late, 0).max() > 0  # a colour is on
    assert derby(late, 2).max() >= 140  # beam motor is turning


def test_derby_strobe_channel_never_leaves_zero_and_white_stays_off_outside_drops(clicks150):
    show, _ = new_show()
    ticks = simulate(show, clicks150)
    assert derby(ticks, 1).max() == 0  # colour-strobe channel is fixed at 0
    assert derby(ticks, 3).max() == 0  # no drop in steady clicks: the white LEDs stay off


def test_silence_and_blackout_key_turn_wash_and_derby_off(clicks150):
    show, _ = new_show()
    ticks = simulate(show, analyse(np.concatenate([click_track(150, 20.0, SR),
                                                   np.zeros(SR * 8, dtype=np.float32)])))
    tail = ticks[-20:]
    assert all(wash(tail, i).max() == 0 for i in range(4))
    assert derby(tail, 0).max() == 0 and derby(tail, 2).max() == 1  # off, motor on "hold"

    show, _ = new_show()

    def press(i, t, s):
        if t > 25 and not s.blackout:
            s.command("b")

    ticks = simulate(show, analyse(click_track(150, 30.0, SR)), on_tick=press)
    tail = ticks[-10:]
    assert all(wash(tail, i).max() == 0 for i in range(4)) and derby(tail, 0).max() == 0


def test_breakdown_darkens_the_derby_and_the_drop_blasts_white():
    show, _ = new_show(drop_blackout_every_s=0.0)
    ticks = simulate(show, breakdown_feats())
    inside = [tk for tk in ticks if 28.0 < tk.t < 31.0]
    assert derby(inside, 0).max() == 0  # bass gone: Derby off
    i0 = round(show._last_drop * 30)
    blast = ticks[i0 + 2:i0 + 12]
    assert all(min(tk.dmx["wash1"][:3]) >= 200 for tk in blast)  # wash snaps to white
    assert (derby(blast, 0) == 58).all() and (derby(blast, 3) == 200).all()  # white + white LEDs
    assert derby(ticks[i0 + 80:], 3).max() == 0  # back to normal afterwards


def test_dark_drop_darkens_wash_and_derby_then_pops_white():
    show, _ = new_show(drop_blackout_every_s=1.0, drop_blackout_s=1.5)
    ticks = simulate(show, breakdown_feats())
    assert show.dark_drops == 1
    i0 = round(show._last_drop * 30)
    dark = ticks[i0 + 3:i0 + 42]
    assert all(wash(dark, i).max() == 0 for i in range(4))
    assert derby(dark, 0).max() == 0 and derby(dark, 3).max() == 0
    pop = ticks[i0 + 48:i0 + 58]
    assert wash(pop, 0).max() >= 200 and (derby(pop, 0) == 58).all()


def test_flash_cap_is_unchanged_by_the_new_lights(clicks150):
    """Wash pulses ride on the existing flash events, which the shared limiter caps."""
    show, _ = new_show()
    ticks = simulate(show, clicks150)
    series = wash(ticks, 0)
    rising = [ticks[i].t for i in range(1, len(series)) if series[i] >= 120 > series[i - 1]]
    gaps = np.diff(rising)
    assert (gaps >= 1 / 8.0 - 1e-6).all()


def test_camera_test_programs_build_and_run():
    from dmxreactive.control.camera_tests import PROGRAMS, build_script

    config = load_config(EXAMPLE)
    for program in PROGRAMS:
        script = build_script(config.patch, program, hold_s=1.0, lead_in_s=0.0)
        assert script.total > 5
        lines = []
        script._announce = lines.append
        t = 0.0
        while t < script.total:
            script.update(t)
            t += 0.025
        assert any("slate" in line for line in lines)
        assert all(sum(f.dmx_values()) >= 0 for f in config.patch.fixtures)


def test_camera_test_derby_writes_raw_motor_value():
    from dmxreactive.control.camera_tests import build_script

    config = load_config(EXAMPLE)
    derby = config.patch.get("derby1")
    script = build_script(config.patch, "derby-motor", hold_s=2.0, lead_in_s=0.0, values=[200])
    script.update(1.5)  # inside the item, after the slate
    assert derby.dmx_values()[derby.profile.channels["motor"].offset] == 200
