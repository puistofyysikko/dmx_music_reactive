from pathlib import Path

import pytest

from dmxreactive.config import load_config
from dmxreactive.dmx.profiles import Fixture, FixtureProfile, Patch, ProfileError
from dmxreactive.dmx.universe import Universe

CONFIG_DIR = Path(__file__).parent.parent / "config"


@pytest.fixture
def mover() -> Fixture:
    return Fixture(FixtureProfile.load(CONFIG_DIR / "fixtures/picospot20_9ch.json"), 1, "mover")


@pytest.fixture
def strobe() -> Fixture:
    return Fixture(FixtureProfile.load(CONFIG_DIR / "fixtures/potstrobe100_3ch.json"), 10, "strobe")


def test_defaults_centre_head_and_dark(mover):
    v = mover.dmx_values()
    assert v[0] == 128 and v[1] == 128  # pan/tilt centre (16-bit 32768 -> 0x80 0x00)
    assert v[2] == 0 and v[3] == 0
    assert v[7] == 0 and v[8] == 0  # dimmer and strobe off


def test_pan_16bit_split(mover):
    mover.set("pan", 1.0)
    assert (mover.dmx_values()[0], mover.dmx_values()[2]) == (255, 255)
    mover.set("pan", 0.0)
    assert (mover.dmx_values()[0], mover.dmx_values()[2]) == (0, 0)
    mover.set("pan", 0.25)
    assert mover.dmx_values()[0] == 64


def test_colour_slots_by_name_match_manual(mover):
    expected = {"white": 5, "red": 16, "blue": 60, "purple": 82}
    for name, dmx in expected.items():
        mover.set("color", name)
        assert mover.dmx_values()[5] == dmx
    with pytest.raises(KeyError):
        mover.set("color", "pink")


def test_strobe_hz_mapping_and_safety_cap(mover):
    mover.set_hz("strobe", 1)
    assert mover.dmx_values()[8] == 10  # 1 Hz = start of strobe range
    mover.set_hz("strobe", 25)  # request the fixture maximum...
    assert mover.dmx_values()[8] == 101  # ...capped just under 10 Hz
    assert mover.max_hz("strobe") == pytest.approx(10.0, abs=0.1)
    assert mover.max_hz("strobe") <= 10.0
    mover.set_hz("strobe", 0)
    assert mover.dmx_values()[8] == 0  # open shutter, no strobe


def test_strobe_light_mode_channel_is_never_touched(strobe):
    assert strobe.dmx_values()[1] == 0
    with pytest.raises(ValueError):
        strobe.set("mode", 0.5)
    strobe.set("strobe", 1.0)  # full range requested...
    assert strobe.dmx_values()[2] == 246  # ...held at the 10 Hz safety cap


def test_blackout_keeps_pan_tilt_but_kills_light(mover):
    mover.set("pan", 0.9)
    mover.set("dimmer", 1.0)
    mover.set_hz("strobe", 5)
    mover.blackout()
    v = mover.dmx_values()
    assert v[7] == 0 and v[8] == 0 and v[0] == round(0.9 * 65535) >> 8


def test_write_places_values_at_patch_address(mover, strobe):
    mover.set("dimmer", 1.0)
    strobe.set("dimmer", 1.0)
    u = Universe()
    Patch([mover, strobe]).write(u)
    assert u.get(8) == 255  # mover dimmer: address 1 + offset 7
    assert u.get(10) == 255  # strobe dimmer: address 10 + offset 0


def test_overlap_and_bounds_rejected(mover):
    other = Fixture(mover.profile, 5, "other")
    with pytest.raises(ProfileError, match="overlap"):
        Patch([mover, other])
    with pytest.raises(ProfileError):
        Fixture(mover.profile, 510, "too-high")  # 9 channels do not fit at 510
    with pytest.raises(ProfileError, match="unique"):
        Patch([mover, Fixture(mover.profile, 20, "mover")])


def test_profile_validation():
    base = {"id": "x", "channel_count": 2, "channels": []}
    FixtureProfile.from_dict(base)
    bad = dict(base, channels=[{"offset": 5, "name": "a", "kind": "continuous"}])
    with pytest.raises(ProfileError, match="outside"):
        FixtureProfile.from_dict(bad)
    dup = dict(base, channels=[{"offset": 0, "name": "a", "kind": "continuous"},
                               {"offset": 0, "name": "b", "kind": "continuous"}])
    with pytest.raises(ProfileError, match="twice"):
        FixtureProfile.from_dict(dup)
    with pytest.raises(ProfileError, match="kind"):
        FixtureProfile.from_dict(dict(base, channels=[{"offset": 0, "name": "a", "kind": "zzz"}]))


def test_example_config_loads():
    cfg = load_config(CONFIG_DIR / "config.example.yaml")
    assert cfg.node.host == "192.168.1.20" and cfg.node.port_address == 1
    assert [f.name for f in cfg.patch.fixtures] == ["mover1", "strobe1"]


def test_set_raw_is_capped_and_zero_is_off(strobe):
    cap = 246
    assert strobe.set_raw("strobe", 255) == cap
    assert strobe.set_raw("strobe", 50) == 50
    assert strobe.set_raw("strobe", 0) == 0 and strobe.dmx_values()[2] == 0


def test_speed_channel_is_inverted_on_the_real_unit(mover):
    mover.set("speed", 1.0)  # fast
    assert mover.dmx_values()[4] == 0
    mover.set("speed", 0.0)  # slow
    assert mover.dmx_values()[4] == 255


def test_set_raw_cap_override_for_calibration(strobe):
    assert strobe.set_raw("strobe", 200, cap=240) == 200
    assert strobe.set_raw("strobe", 255, cap=240) == 240


def test_pot_strobe_period_mapping_matches_camera_measurements(strobe):
    """Measured with the a6000 (60 fps): DMX 150/190/220/235 -> ~634/417/250/167 ms."""
    for dmx, period_ms in ((150, 634), (190, 417), (220, 250), (235, 167)):
        hz = 1000 / period_ms
        strobe.set_hz("strobe", hz)
        assert abs(strobe.dmx_values()[2] - dmx) <= 3
    assert strobe.max_hz("strobe") <= 10.0
    assert strobe.max_hz("strobe") > 9.5


def test_hz_cap_never_exceeded_for_any_request(mover, strobe):
    for f in (mover, strobe):
        for hz in (0.5, 5, 10, 15, 25, 100):
            f.set_hz("strobe", hz)
            assert f.dmx_values()[f.profile.channels["strobe"].offset] <= f._rate_cap(
                f.profile.channels["strobe"])
        assert f.max_hz("strobe") <= 10.0
