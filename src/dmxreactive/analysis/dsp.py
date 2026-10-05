"""Small DSP building blocks: adaptive normalisation and adaptive-threshold onset detection."""

from __future__ import annotations

from collections import deque

import numpy as np


class AdaptiveNorm:
    """Maps a dB value to 0..1 relative to a decaying peak, so results don't depend on volume.

    The ceiling follows peaks instantly and falls by `decay_db_per_s`; the floor is `range_db`
    below the ceiling.
    """

    def __init__(self, frame_rate: float, range_db: float = 36.0, decay_db_per_s: float = 2.0,
                 min_ceiling_db: float = -60.0) -> None:
        self._decay = decay_db_per_s / frame_rate
        self._range = range_db
        self._ceil = min_ceiling_db
        self._min_ceiling = min_ceiling_db

    def __call__(self, db: float) -> float:
        self._ceil = max(db, self._ceil - self._decay, self._min_ceiling)
        return float(np.clip((db - (self._ceil - self._range)) / self._range, 0.0, 1.0))


class PeakNorm:
    """Divides a non-negative signal by its decaying peak (linear domain), result 0..1."""

    def __init__(self, frame_rate: float, half_life_s: float = 4.0, floor: float = 1e-6) -> None:
        self._k = 0.5 ** (1.0 / (half_life_s * frame_rate))
        self._peak = floor
        self._floor = floor

    def __call__(self, x: float) -> float:
        self._peak = max(x, self._peak * self._k, self._floor)
        return min(1.0, x / self._peak)


class OnsetDetector:
    """Adaptive-threshold onset detector for a normalised flux signal (0..1).

    Fires on the first frame the flux exceeds mean + k*std of the last `window_s` seconds
    (and an absolute minimum), at most once per `refractory_s`.
    """

    def __init__(self, frame_rate: float, window_s: float = 1.0, k: float = 1.5,
                 min_level: float = 0.25, refractory_s: float = 0.12) -> None:
        self._hist: deque[float] = deque(maxlen=max(4, round(window_s * frame_rate)))
        self._k = k
        self._min = min_level
        self._refractory = round(refractory_s * frame_rate)
        self._since = 10**6
        self._prev = 0.0

    def __call__(self, x: float) -> bool:
        self._since += 1
        fired = False
        if len(self._hist) >= 4:
            h = np.fromiter(self._hist, dtype=np.float64)
            thr = max(self._min, float(h.mean() + self._k * h.std()))
            if x > thr and x > self._prev and self._since > self._refractory:
                fired = True
                self._since = 0
        self._hist.append(x)
        self._prev = x
        return fired
