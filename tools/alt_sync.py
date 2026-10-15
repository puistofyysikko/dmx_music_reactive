"""Relative lag of two lights from a clip of `dmxreactive camera-test wash-strobe`.

    python tools/alt_sync.py clip.mp4 [--roi w:h:x:y]

The program flashes light A (the wash) on every full second and light B (the strobe) half a
second later. Whole-frame brightness onsets therefore alternate A, B, A, B...; if B lags A by
d ms the intervals alternate 500 + d and 500 - d, so d = (mean A->B minus mean B->A) / 2. No
audio and no click-period aliasing involved; frame quantisation of the camera is the limit.
"""

from __future__ import annotations

import argparse
import statistics

import numpy as np
from video_luma import luma_series


def onsets(t: np.ndarray, y: np.ndarray, frac: float = 0.25) -> np.ndarray:
    span = y.max() - y.min()
    fps = (len(t) - 1) / (t[-1] - t[0])
    out, prev = [], -10**9
    for i in range(1, len(y)):
        if y[i] - y[i - 1] >= frac * span and i - prev > max(2, int(0.2 * fps)):
            out.append(t[i])
            prev = i
    return np.array(out)


def relative_lag_ms(on: np.ndarray) -> tuple[float, float, int]:
    """(lag of B behind A in ms, spread of the per-pair estimates, pairs). `on` alternates A, B."""
    iv = np.diff(on) * 1000.0
    ab, ba = iv[0::2], iv[1::2]
    n = min(len(ab), len(ba))
    est = (ab[:n] - ba[:n]) / 2.0
    return float(statistics.median(est)), float(np.percentile(est, 90) - np.percentile(est, 10)), n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("--roi", help="crop w:h:x:y (source pixels)")
    args = ap.parse_args()
    box = tuple(int(v) for v in args.roi.split(":")) if args.roi else None
    t, y = luma_series(args.video, box)
    on = onsets(t, y)
    # the program's own slate (a wash flash) comes first, then the pulses start with the wash
    on = on[1:]
    if len(on) < 8:
        print("too few flash onsets; check the clip/ROI")
        return 1
    lag, spread, n = relative_lag_ms(on)
    print(f"{len(on)} onsets, {n} A/B pairs: B (strobe) lags A (wash) by {lag:+.1f} ms "
          f"(p10..p90 spread {spread:.0f} ms; one frame at 240 fps = 4.2 ms)")
    print("  (+ = strobe later than wash; the first onset of the clip is the slate and is dropped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
