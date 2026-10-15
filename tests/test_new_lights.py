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


# -- wear-free modes ---------------------------------------------------------------------
def mover_ch(ticks, idx):
    return np.array([tk.dmx["mover1"][idx] for tk in ticks])


def test_static_mode_parks_the_head_and_stops_every_motor_and_wheel():
    feats = breakdown_feats()  # beats, a breakdown, a drop blast and beats again
    show, _ = new_show(mechanics="static")
    ticks = [tk for tk in simulate(show, feats) if tk.t > 6]
    assert len({int(v) for v in mover_ch(ticks, 0)}) == 1  # pan parked
    assert len({int(v) for v in mover_ch(ticks, 1)}) == 1  # tilt parked
    assert set(mover_ch(ticks, 5)) == {5}  # colour wheel fixed on white
    assert set(mover_ch(ticks, 6)) == {8}  # gobo wheel fixed on open (also during the drop)
    assert set(derby(ticks, 2)) == {1}  # Derby motor on "hold" the whole time
    assert derby(ticks, 0).max() > 0  # ...but its LEDs still play
    assert mover_ch(ticks, 7).max() > 100 and show.flashes > 0  # dimmer and flashes still work


def test_full_mode_still_moves_for_comparison():
    show, _ = new_show()
    ticks = [tk for tk in simulate(show, breakdown_feats()) if tk.t > 6]
    assert len({int(v) for v in mover_ch(ticks, 0)}) > 5
    assert derby(ticks, 2).max() >= 140


def test_off_mode_keeps_the_head_and_the_derby_dark_even_in_a_drop_blast():
    show, _ = new_show(mechanics="off")
    ticks = simulate(show, breakdown_feats())
    assert mover_ch(ticks, 7).max() == 0 and mover_ch(ticks, 8).max() == 0  # no light, no strobe
    assert derby(ticks, 0).max() == 0 and set(derby(ticks, 2)) == {1}
    assert derby(ticks, 3).max() == 0  # not even the white LEDs of the blast
    assert max(max(wash(ticks, i)) for i in range(4)) > 100  # the LED lights carry on
    assert np.array([tk.dmx["strobe1"][0] for tk in ticks]).max() > 100


def test_mechanics_key_cycles_and_takes_effect_live(clicks150):
    show, _ = new_show()
    assert [show.command("m") for _ in range(3)] == [
        "mechanics: static", "mechanics: off", "mechanics: full"]

    show, _ = new_show()

    def press(i, t, s):
        if abs(t - 20) < 0.02:
            s.command("m")

    ticks = simulate(show, clicks150, on_tick=press)
    late = [tk for tk in ticks if tk.t > 26]
    assert len({int(v) for v in mover_ch(late, 0)}) == 1 and set(derby(late, 2)) == {1}
    assert len({int(v) for v in mover_ch([tk for tk in ticks if 12 < tk.t < 19], 0)}) > 3


def test_config_rejects_unknown_mechanics(tmp_path):
    import yaml

    from dmxreactive.dmx.profiles import ProfileError

    raw = yaml.safe_load(EXAMPLE.read_text())
    raw["show"]["mechanics"] = "gentle"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    (tmp_path / "fixtures").symlink_to(ROOT / "config" / "fixtures")
    with pytest.raises(ProfileError):
        load_config(cfg)
    raw["show"]["mechanics"] = "static"
    cfg.write_text(yaml.safe_dump(raw))
    assert load_config(cfg).show.mechanics == "static"


def test_wash_strobe_program_alternates_the_two_lights():
    from dmxreactive.control.camera_tests import build_script

    config = load_config(EXAMPLE)
    script = build_script(config.patch, "wash-strobe", lead_in_s=0.0)
    strobe, wash_fx = config.patch.get("strobe1"), config.patch.get("wash1")
    seen = []
    t = 0.0
    while t < script.total:
        script.update(t)
        seen.append((t, max(wash_fx.dmx_values()), strobe.dmx_values()[0]))  # wash rgb, dimmer
        t += 0.025
    t0 = 1.0  # after the slate
    wash_on = [t for t, w, _ in seen if t0 + 3 <= t < t0 + 4 and w > 0]
    strobe_on = [t for t, _, s in seen if t0 + 3 <= t < t0 + 4 and s > 0]
    assert wash_on and strobe_on
    assert max(wash_on) < min(strobe_on)  # the wash pulses first, the strobe half a second later
    assert 0.45 <= min(strobe_on) - min(wash_on) <= 0.55


def test_alt_sync_recovers_a_known_lag():
    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    from alt_sync import relative_lag_ms

    lag = 0.012  # strobe 12 ms behind the wash
    on = np.array([k + (0.5 + lag if k % 2 else 0.0) for k in range(0, 1)] +
                  [x for i in range(20) for x in (float(i) + 1, float(i) + 1.5 + lag)])
    got, _, n = relative_lag_ms(on[1:])
    assert n >= 15 and abs(got - 12.0) < 0.5
