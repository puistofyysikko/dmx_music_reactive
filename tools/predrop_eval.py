"""Offline evaluation of the "pre-drop kill" idea on cached features (tools/predrop_features.py).

For every breakdown the show's drop detector would see (same logic as Show._detect_drop) it
reports where the breakdown started, where the bass returned, how long that is in bars, and
whether the return lands on a bar "one". Then it scores simple predictors that, knowing only the
breakdown start (and the bar grid), name the beat to kill the lights on:

    python tools/predrop_eval.py feats/*.npz
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

DIP, HIGH_GAP, MIN_RUN_S, HOLD_S, TAU = 0.25, 0.12, 1.0, 0.4, 0.6


def breakdowns(t: np.ndarray, bass: np.ndarray):
    """Yield (start_s, return_s) of breakdowns as the show's drop detector sees them."""
    dt = np.median(np.diff(t))
    a = 1 - np.exp(-dt / TAU)
    ef = np.empty_like(bass)
    ef[0] = bass[0]
    for i in range(1, len(bass)):
        ef[i] = ef[i - 1] + a * (bass[i] - ef[i - 1])
    n30 = int(30 / dt)
    run = 0.0
    high = 0.0
    run_start = None
    ref = 0.0
    for i in range(len(ef)):
        if i % 5 == 0 and i >= 150:
            ref = float(np.percentile(ef[max(0, i - n30):i + 1], 85))
        if i < 150:
            continue
        gap = ref - ef[i]
        if gap >= DIP:
            if run == 0.0:
                run_start = t[i]
            run += dt
            high = 0.0
        elif gap <= HIGH_GAP:
            high += dt
            if high >= HOLD_S:
                if run >= MIN_RUN_S:
                    yield run_start, t[i] - HOLD_S
                run = 0.0
                high = 0.0
        else:
            high = 0.0


def main() -> None:
    rows = []
    for path in sys.argv[1:]:
        d = np.load(path)
        c = d["cols"]
        t, bass, bpm, beat, bar_beat, conf = c[:, 0], c[:, 1], c[:, 2], c[:, 3], c[:, 4], c[:, 5]
        ones = t[(beat > 0) & (bar_beat == 0) & (conf >= 0.35)]
        print(f"\n== {Path(path).stem}")
        print("  start   return  bpm   beats  bars   off-4bar(beats)  return-vs-one(beats)")
        for ts, tr in breakdowns(t, bass):
            m = (t >= ts) & (t <= tr) & (bpm > 0)
            b = float(np.median(bpm[m])) if m.any() else 0.0
            if b <= 0:
                print(f"  {ts:6.1f} {tr:6.1f}  (no tempo)")
                continue
            beat_s = 60.0 / b
            beats = (tr - ts) / beat_s
            near4 = round(beats / 16) * 16
            off4 = beats - near4
            vs_one = float("nan")
            if len(ones):
                k = np.argmin(np.abs(ones - tr))
                vs_one = (tr - ones[k]) / beat_s
            rows.append((Path(path).stem, ts, tr, b, beats, off4, vs_one))
            print(f"  {ts:6.1f} {tr:6.1f} {b:5.1f} {beats:6.1f} {beats / 4:5.1f}   {off4:+6.1f}"
                  f"            {vs_one:+5.2f}")
    if rows:
        beats = np.array([r[4] for r in rows])
        print(f"\n{len(rows)} breakdowns; |off a multiple of 4 bars| <= 1 beat: "
              f"{np.mean(np.abs([r[5] for r in rows]) <= 1.0) * 100:.0f} %")


if __name__ == "__main__":
    main()
