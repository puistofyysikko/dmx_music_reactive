"""Level helpers shared by the meter and (later) the analyser."""

from __future__ import annotations

import numpy as np

FLOOR_DB = -100.0


def rms_dbfs(block: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return max(FLOOR_DB, 20 * np.log10(rms)) if rms > 0 else FLOOR_DB


def peak_dbfs(block: np.ndarray) -> float:
    peak = float(np.max(np.abs(block)))
    return max(FLOOR_DB, 20 * np.log10(peak)) if peak > 0 else FLOOR_DB
