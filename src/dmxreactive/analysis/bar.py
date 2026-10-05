"""Bar (downbeat) tracking on top of the beat grid: which beat of the 4-beat bar is the "one"?

Assumes 4/4 (dance music). Every beat of the grid is summarised (spectrum, bass energy, onset
strength, kick strength) and two kinds of evidence vote for the bar phase (beat index mod 4):

* change: sounds change on bar and phrase boundaries, so a *peak* in the spectral change between
  the two beats after a boundary and the two before it is evidence that the boundary is a one
  (only local maxima vote, otherwise a change blurs over neighbouring beats);
* accent: the one is often a little stronger than the other beats (onset, low end, kick).

Votes accumulate per phase with slow decay; the winning phase is switched only with hysteresis,
so the "one" stays put once found. `confidence` says how clear the vote is. Decisions about a
boundary are made three beats late (peak picking needs the beats after it), which does not matter
for a phase that stays fixed for minutes.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

DECAY = 0.985  # per beat: memory of ~65 beats (16 bars)
W_CHANGE = 1.0
W_ACCENT = 0.15
PEAK_FACTOR = 1.2  # a change peak must exceed the recent median change by this factor
SWITCH_MARGIN = 1.25  # a challenger phase must lead the current one by this factor...
SWITCH_BEATS = 8  # ...for this many consecutive beats
WINDOW = 2  # beats compared on each side of a boundary


@dataclass(frozen=True)
class BarState:
    bar_beat: int  # 0..3, 0 = the one; -1 = unknown
    bar_confidence: float  # 0..1
    bar_start: bool  # True on the frame of a beat that is a one
    bar_count: int  # number of ones seen


class _Running:
    """Running z-score over the last N beats."""

    def __init__(self, n: int = 24) -> None:
        self._v: deque[float] = deque(maxlen=n)

    def z(self, x: float) -> float:
        self._v.append(x)
        if len(self._v) < 6:
            return 0.0
        a = np.fromiter(self._v, dtype=np.float64)
        return float((x - a.mean()) / (a.std() + 1e-6))


class BarTracker:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._index = -1  # beat index of the current interval (from the tempo tracker)
        self._epoch = -1
        self._score = np.zeros(4)
        self._offset = 0
        self._challenger = -1
        self._challenger_run = 0
        self._beats: deque[tuple[int, dict]] = deque(maxlen=WINDOW + 8)  # finished intervals
        self._novelty: deque[tuple[int, float]] = deque(maxlen=48)  # (beat index, change)
        self._acc = {"flux": _Running(), "bass": _Running(), "kick": _Running()}
        self._accents: dict[int, float] = {}
        self._conf = 0.0
        self._bar_count = 0
        self._reset_interval()

    def _reset_interval(self) -> None:
        self._frames = 0
        self._flux_peak = 0.0
        self._bass_sum = 0.0
        self._kick_max = 0.0
        self._spec: np.ndarray | None = None

    def _finalize(self) -> dict | None:
        if self._frames == 0 or self._spec is None:
            return None
        return {"flux": self._flux_peak, "bass": self._bass_sum / self._frames,
                "kick": self._kick_max, "spec": self._spec / self._frames}

    def update(self, index: int, epoch: int, active: bool, flux: float, bass_energy: float,
               spec: np.ndarray, kick_strength_new: float) -> BarState:
        """Call once per analysis frame. `index`/`epoch` come from the tempo tracker (a running
        beat number that does not slip, and a counter that changes when the grid is re-locked);
        `active` = the beat grid is valid (tempo known, music on); `spec` = a coarse log-spaced
        spectrum (any fixed number of bands)."""
        if not active or epoch != self._epoch:
            self.reset()
            self._epoch = epoch
            if not active:
                return BarState(-1, 0.0, False, 0)
        start = False
        if self._index < 0:
            self._index = index  # first frame of a fresh grid
        elif index != self._index:  # a new beat began
            summary = self._finalize()
            self._reset_interval()
            if summary is not None:
                self._beat_done(self._index, summary)
            self._index = index
            start = (index - self._offset) % 4 == 0
            if start:
                self._bar_count += 1
        self._frames += 1
        if self._frames <= 4:  # the onset right after the beat
            self._flux_peak = max(self._flux_peak, flux)
        self._bass_sum += bass_energy
        self._kick_max = max(self._kick_max, kick_strength_new)
        self._spec = spec.copy() if self._spec is None else self._spec + spec
        return BarState((self._index - self._offset) % 4, self._conf, start, self._bar_count)

    # -- evidence ----------------------------------------------------------------------------
    def _beat_done(self, idx: int, s: dict) -> None:
        self._beats.append((idx, s))
        self._accents[idx] = (self._acc["flux"].z(s["flux"]) + self._acc["bass"].z(s["bass"])
                              + self._acc["kick"].z(s["kick"]))
        if len(self._accents) > 16:
            del self._accents[min(self._accents)]
        beats = list(self._beats)
        # change at the boundary that starts beat k = idx - (WINDOW - 1): the WINDOW beats from k
        # on versus the WINDOW beats before it (all four intervals must be finished and adjacent)
        if len(beats) >= 2 * WINDOW:
            seq = beats[-2 * WINDOW:]
            if all(b[0] - a[0] == 1 for a, b in zip(seq, seq[1:], strict=False)):
                before = np.mean([x[1]["spec"] for x in seq[:WINDOW]], axis=0)
                after = np.mean([x[1]["spec"] for x in seq[WINDOW:]], axis=0)
                self._novelty.append((seq[WINDOW][0], float(np.abs(after - before).sum())))
        self._decide()

    def _decide(self) -> None:
        nov = list(self._novelty)
        self._score *= DECAY
        if len(nov) >= 8:
            values = np.array([v for _, v in nov])
            median = float(np.median(values)) + 1e-9
            # test the candidate three beats back: its neighbours (+-2) are known by now
            if len(nov) >= 5:
                k_idx, k_val = nov[-3]
                around = [v for i, v in nov if abs(i - k_idx) <= 2]
                if k_val >= max(around) and k_val >= PEAK_FACTOR * median:
                    self._score[k_idx % 4] += W_CHANGE * k_val / median
                    self._score[k_idx % 4] += W_ACCENT * self._accents.get(k_idx, 0.0)
        best = int(np.argmax(self._score))
        order = np.sort(self._score)
        total = abs(order[-1]) + abs(order[-2]) + 1e-6
        self._conf += 0.1 * (float(np.clip((order[-1] - order[-2]) / total * 2.0, 0.0, 1.0))
                             - self._conf)
        if best != self._offset:
            cur = self._score[self._offset]
            if self._score[best] > SWITCH_MARGIN * max(cur, 1e-9) + 1e-9:
                if best == self._challenger:
                    self._challenger_run += 1
                else:
                    self._challenger, self._challenger_run = best, 1
                if self._challenger_run >= SWITCH_BEATS:
                    self._offset, self._challenger_run = best, 0
            else:
                self._challenger_run = 0
        else:
            self._challenger_run = 0
