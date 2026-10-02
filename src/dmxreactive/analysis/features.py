"""The per-block feature snapshot the effect engine consumes."""

from __future__ import annotations

from dataclasses import dataclass

BAND_NAMES = ("bass", "lowmid", "mid", "high", "air")


@dataclass(frozen=True)
class Features:
    t: float  # audio time of the end of the analysed block, seconds
    silent: bool
    level_db: float  # RMS in dBFS
    level: float  # 0..1, relative to the recent loudness range (volume-independent)
    bands: tuple[float, ...]  # 0..1 per BAND_NAMES, each adaptively normalised
    band_db: tuple[float, ...]  # absolute band power (dB, uncalibrated), for debugging/tests
    centroid: float  # 0 (dark/bassy) .. 1 (bright), log-scaled spectral centroid
    flux: float  # 0..1 overall onset strength
    # Events carry a monotonically increasing counter so a slower consumer (30 Hz render loop
    # vs ~47 Hz analysis) never misses one: compare the counter with the last one seen.
    kick: bool
    kick_strength: float
    kick_count: int
    hat: bool
    hat_count: int
    bpm: float
    beat_phase: float  # 0 at a beat, rising to 1 just before the next
    beat: bool
    beat_count: int
    beat_confidence: float  # 0..1 (strength of the periodicity; see beat_locked for trust)
    beat_locked: bool  # True when kicks consistently fall on the predicted beat grid
    energy_trend: float  # -1 falling (breakdown) .. +1 rising (build-up)

    def band(self, name: str) -> float:
        return self.bands[BAND_NAMES.index(name)]
