import time
from pathlib import Path

import numpy as np

from dmxreactive.analysis.analyzer import Analyzer
from dmxreactive.audio.array_source import ArraySource, click_track
from dmxreactive.config import load_config
from dmxreactive.control.tui import render_to_text
from dmxreactive.engine.show import Show
from dmxreactive.live import AnalysisThread, FeatureHolder, LiveState, status_line

EXAMPLE = Path(__file__).parent.parent / "config" / "config.example.yaml"
SR, BLOCK = 48000, 1024


def make_state(feats=None, steps=0):
    cfg = load_config(EXAMPLE)
    holder = FeatureHolder()
    src = ArraySource(np.zeros(SR, dtype=np.float32), SR, BLOCK, realtime=False)
    analysis = AnalysisThread(src, Analyzer(SR, BLOCK), holder)
    state = LiveState(cfg, Show(cfg.patch, cfg.show), holder, src, analysis,
                      target="127.0.0.1:6454")
    state.keys_active = True
    if feats is not None:
        holder.set(feats)
    for _ in range(steps):
        state.show.step(feats, 0.0, 1 / 40)
    return state


def last_features(seconds=25.0):
    an = Analyzer(SR, BLOCK)
    x = click_track(150, seconds, SR)
    feats = [an.process(x[i:i + BLOCK]) for i in range(0, len(x) - BLOCK + 1, BLOCK)]
    return feats[-1]


def test_panel_shows_audio_scene_and_fixtures():
    state = make_state(last_features(), steps=40)
    state.show.command("b")
    text = render_to_text(state)
    for word in ("LIVE", "BPM", "LOCKED", "mover1", "strobe1", "BLACKOUT", "master", "blackout"):
        assert word in text, word


def test_panel_without_audio_says_so_and_does_not_crash():
    text = render_to_text(make_state(None))
    assert "no audio" in text and "mover1" in text


def test_status_line_plain():
    line = status_line(make_state(last_features(), steps=10))
    assert "BPM" in line and "master" in line


def test_feature_holder_delays_features():
    holder = FeatureHolder(delay_s=0.2)
    f = last_features(12.0)
    holder.set(f)
    assert holder.get() == (None, 0.0)  # too fresh: the lights wait for the late sound
    time.sleep(0.25)
    got, age = holder.get()
    assert got is f and 0.2 <= age < 0.4


def test_feature_holder_without_delay_is_immediate():
    holder = FeatureHolder()
    f = last_features(12.0)
    holder.set(f)
    got, age = holder.get()
    assert got is f and age < 0.05


def test_show_logs_events_for_the_ui():
    cfg = load_config(EXAMPLE)
    show = Show(cfg.patch, cfg.show)
    show.command("s")
    show.command("3")
    assert [m for _, m in show.events][-2:] == ["strobe light OFF", "scene: drive (forced)"]
