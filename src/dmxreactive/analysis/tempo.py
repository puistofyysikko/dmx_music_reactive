"""Tempo and beat-phase tracking on an onset-strength envelope.

Every `update_every` frames a comb filter bank scores candidate (tempo, phase) pairs against
the last ~8 s of the envelope; the best pair (after a tempo prior and hysteresis) drives a
free-running phase that advances every frame. Beats are therefore *predicted* on the grid, so
a missed or weak kick doesn't drop a beat, and they arrive without detection delay.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

BPM_MIN = 70.0
BPM_MAX = 200.0
BPM_STEP = 0.5
PRIOR_CENTRE_BPM = 140.0
PRIOR_SIGMA_OCT = 0.7  # width of the log-tempo prior, in octaves


@dataclass(frozen=True)
class BeatState:
    bpm: float
    phase: float  # 0..1, 0 = on the beat
    beat: bool
    confidence: float


class TempoTracker:
    def __init__(self, frame_rate: float, history_s: float = 8.0, update_every: int = 6) -> None:
        self.fr = frame_rate
        self._n = round(history_s * frame_rate)
        self._env = np.zeros(self._n)
        self._update_every = update_every
        self._frames = 0
        self._bpms = np.arange(BPM_MIN, BPM_MAX + BPM_STEP, BPM_STEP)
        octaves = np.log2(self._bpms / PRIOR_CENTRE_BPM)
        self._prior = np.exp(-0.5 * (octaves / PRIOR_SIGMA_OCT) ** 2)
        self._bpm = 0.0
        self._phase = 0.0
        self._confidence = 0.0
        self._challenger = 0.0
        self._challenger_hits = 0
        self._since_beat = 10**6
        self._have_lock = False
        self._confident_updates = 0
        self._established = False

    def reset(self) -> None:
        """Forget everything (silence / new track)."""
        self._env[:] = 0
        self._bpm = 0.0
        self._phase = 0.0
        self._confidence = 0.0
        self._challenger, self._challenger_hits = 0.0, 0
        self._have_lock = False
        self._confident_updates = 0
        self._established = False
        self._frames = 0

    def _score(self) -> tuple[np.ndarray, np.ndarray]:
        """Best phase offset (frames since the last beat) and score for every candidate BPM."""
        env, n = self._env, self._n
        best_score = np.zeros(len(self._bpms))
        best_phase = np.zeros(len(self._bpms))
        for i, bpm in enumerate(self._bpms):
            p = 60.0 * self.fr / bpm  # period in frames
            n_taps = int((n - 1) // p)
            if n_taps < 6:
                continue
            j = np.arange(n_taps)
            weights = np.exp(-j * p / (self.fr * 6.0))  # recent beats count more (6 s decay)
            phases = np.arange(0.0, p, 0.5)
            pos = (n - 1) - phases[:, None] - j[None, :] * p  # (phases, taps)
            lo = np.floor(pos).astype(int)
            frac = pos - lo
            vals = env[lo] * (1 - frac) + env[np.minimum(lo + 1, n - 1)] * frac
            scores = vals @ weights / weights.sum()
            k = int(np.argmax(scores))
            best_score[i], best_phase[i] = scores[k], phases[k]
        return best_score, best_phase

    def update(self, onset: float) -> BeatState:
        self._env = np.roll(self._env, -1)
        self._env[-1] = onset
        self._frames += 1
        beat = False
        if self._frames % self._update_every == 0 and self._frames >= self._n // 2:
            self._estimate()
        if self._bpm > 0:
            prev = self._phase
            self._phase += self._bpm / 60.0 / self.fr
            self._since_beat += 1
            if self._phase >= 1.0:
                self._phase -= 1.0
                period = 60.0 * self.fr / self._bpm
                if self._since_beat >= 0.6 * period and prev > 0.5:
                    beat = True
                    self._since_beat = 0
        return BeatState(self._bpm, self._phase, beat, self._confidence)

    def _estimate(self) -> None:
        scores, phases = self._score()
        weighted = scores * self._prior
        if weighted.max() <= 1e-9:
            self._confidence *= 0.9
            return
        k = int(np.argmax(weighted))
        mean_score = float(scores[scores > 0].mean())
        conf = float(np.clip((scores[k] / max(mean_score, 1e-9) - 1.0) / 2.5, 0.0, 1.0))
        cand = float(self._bpms[k])
        # Hysteresis: switch tempo only if a different one wins repeatedly.
        if self._bpm == 0.0:
            self._bpm = cand
        elif abs(cand - self._bpm) > 1.0:
            if abs(cand - self._challenger) <= 1.0:
                self._challenger_hits += 1
            else:
                self._challenger, self._challenger_hits = cand, 1
            ratio = weighted[k] / max(weighted[int(np.argmin(abs(self._bpms - self._bpm)))], 1e-9)
            # Once a tempo is established (a breakdown must not flip it) demand long evidence.
            need_hits, need_ratio = (14, 1.3) if self._established else (3, 1.1)
            if self._challenger_hits >= need_hits and ratio > need_ratio:
                self._bpm = cand
                self._have_lock = False
        else:
            self._bpm += 0.3 * (cand - self._bpm)  # follow slow drift
            self._challenger_hits = 0
        self._confidence += 0.5 * (conf - self._confidence)
        self._confident_updates = self._confident_updates + 1 if self._confidence >= 0.45 else 0
        if self._confident_updates >= 16:
            self._established = True
        # Phase: estimated position (fraction of a period since the last beat) vs ours.
        p = 60.0 * self.fr / self._bpm
        est_k = int(np.argmin(abs(self._bpms - self._bpm)))
        est = (phases[est_k] % p) / p
        # The comb measured "frames since the last beat" at the newest frame; our phase is
        # also "fraction since last beat", so they compare directly.
        if not self._have_lock or conf < 0.05:
            if conf >= 0.05 or not self._have_lock:
                self._phase = est
                self._have_lock = conf >= 0.05
        else:
            err = (est - self._phase + 0.5) % 1.0 - 0.5
            self._phase = (self._phase + 0.2 * err) % 1.0


def bpm_to_period_frames(bpm: float, frame_rate: float) -> float:
    return 60.0 * frame_rate / bpm if bpm > 0 else math.inf
