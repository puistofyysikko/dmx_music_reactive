import time

import numpy as np
import pytest

from dmxreactive.analysis.analyzer import Analyzer
from dmxreactive.analysis.dsp import AdaptiveNorm, OnsetDetector
from dmxreactive.analysis.features import BAND_NAMES
from dmxreactive.audio.array_source import click_track, sine

SR, BLOCK = 48000, 1024


def run(x: np.ndarray) -> list:
    an = Analyzer(SR, BLOCK)
    return [an.process(x[i:i + BLOCK]) for i in range(0, len(x) - BLOCK + 1, BLOCK)]


@pytest.mark.parametrize("bpm", [100, 140, 174])
def test_click_track_tempo_and_beat_timing(bpm):
    feats = run(click_track(bpm, 26.0, SR))
    final = np.median([f.bpm for f in feats[-40:]])
    assert abs(final - bpm) <= 1.0
    assert feats[-1].beat_confidence > 0.8
    true = np.arange(0, 26.0, 60 / bpm)
    beats = [f.t for f in feats if f.beat and 12 < f.t < 25]
    assert len(beats) >= 0.8 * (13 * bpm / 60)
    off = np.array([(b - true[np.argmin(abs(true - b))]) * 1000 for b in beats])
    # grid beats land at/after the true click by < one analysis frame (21 ms), never early
    assert off.min() > -8 and off.max() < 30
    kicks = [f.t for f in feats if f.kick and f.t > 12]
    koff = np.array([(k - true[np.argmin(abs(true - k))]) * 1000 for k in kicks])
    assert len(kicks) >= 0.9 * len(beats) and 0 < np.median(koff) < 35


def test_tempo_follows_a_change_and_ignores_nothing_in_between():
    x = np.concatenate([click_track(120, 20.0, SR), click_track(150, 30.0, SR)])
    feats = run(x)
    assert abs(np.median([f.bpm for f in feats if 15 < f.t < 20]) - 120) <= 1
    assert abs(np.median([f.bpm for f in feats if f.t > 45]) - 150) <= 1


def test_silence_has_no_tempo_and_no_events():
    feats = run(np.zeros(SR * 5, dtype=np.float32))
    assert feats[-1].silent and feats[-1].bpm == 0 and feats[-1].beat_count == 0
    assert feats[-1].kick_count == 0 and feats[-1].level == 0


def test_silence_after_music_resets_tempo():
    x = np.concatenate([click_track(140, 20.0, SR), np.zeros(SR * 4, dtype=np.float32)])
    feats = run(x)
    assert feats[int(19 * SR / BLOCK)].bpm > 0
    assert feats[-1].silent and feats[-1].bpm == 0


def test_volume_independence():
    x = click_track(140, 22.0, SR) + 0.05 * sine(3000, 22.0, SR)
    loud, quiet = run(x), run(0.1 * x)  # 20 dB quieter
    assert abs(np.median([f.bpm for f in loud[-40:]]) - np.median([f.bpm for f in quiet[-40:]])) < 1
    lb = np.mean([f.bands for f in loud[-200:]], axis=0)
    qb = np.mean([f.bands for f in quiet[-200:]], axis=0)
    assert np.allclose(lb, qb, atol=0.15)
    assert abs(sum(f.kick for f in loud) - sum(f.kick for f in quiet)) <= 2


@pytest.mark.parametrize("freq,band", [(60, "bass"), (300, "lowmid"), (1000, "mid"),
                                       (4000, "high"), (11000, "air")])
def test_tone_excites_its_band(freq, band):
    f = run(sine(freq, 4.0, SR, 0.3))[-1]
    assert BAND_NAMES[int(np.argmax(f.band_db))] == band


def test_centroid_orders_dark_and_bright():
    dark, bright = run(sine(100, 3.0, SR))[-1], run(sine(6000, 3.0, SR))[-1]
    assert dark.centroid < 0.3 < 0.7 < bright.centroid


def test_event_counters_are_monotonic():
    feats = run(click_track(150, 12.0, SR))
    for name in ("kick_count", "hat_count", "beat_count"):
        vals = [getattr(f, name) for f in feats]
        assert vals == sorted(vals)
    assert feats[-1].kick_count == sum(f.kick for f in feats)
    assert feats[-1].beat_count == sum(f.beat for f in feats)


def test_beat_phase_is_a_sawtooth_wrapping_on_beats():
    feats = [f for f in run(click_track(120, 24.0, SR)) if f.t > 15]
    assert all(0 <= f.beat_phase < 1 for f in feats)
    wraps = [i for i in range(1, len(feats)) if feats[i].beat]
    for i in wraps:
        assert feats[i].beat_phase < 0.2 and feats[i - 1].beat_phase > 0.8


def test_adaptive_norm_ignores_absolute_level():
    n1, n2 = AdaptiveNorm(47, 24.0), AdaptiveNorm(47, 24.0)
    for _ in range(200):
        a, b = n1(-10.0), n2(-40.0)
    assert a == pytest.approx(b, abs=0.01) == pytest.approx(1.0, abs=0.01)
    assert n1(-22.0) == pytest.approx(0.5, abs=0.02)


def test_onset_detector_fires_on_spikes_not_on_noise_and_respects_refractory():
    det = OnsetDetector(47)
    rng = np.random.default_rng(0)
    fired = [det(float(v)) for v in 0.05 * rng.random(200)]
    assert not any(fired)
    spikes = []
    for i in range(100):
        spikes.append(det(1.0 if i % 20 == 5 else 0.02))
    assert sum(spikes) == 5
    det2 = OnsetDetector(47, refractory_s=0.2)
    for _ in range(60):
        det2(0.0)
    assert [det2(1.0), det2(0.0), det2(1.0)] == [True, False, False]  # second spike too soon


def test_analysis_fits_the_frame_budget():
    x = click_track(150, 12.0, SR) + 0.1 * sine(2000, 12.0, SR)
    t0 = time.perf_counter()
    feats = run(x)
    per_block_ms = (time.perf_counter() - t0) / len(feats) * 1000
    assert per_block_ms < 8.0  # budget is 21 ms per block; stay well under half
