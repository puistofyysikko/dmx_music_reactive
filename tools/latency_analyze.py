"""Measure click-to-flash latency from a video that shows the lights and records the sound.

    python tools/latency_analyze.py clip.mp4 [--lead 30] [--roi w:h:x:y] [--skip 15]

Film the lit wall (or the strobe) with the click track audible in the room. Works best with a
high-frame-rate camera (GoPro 240 fps: ~4 ms resolution). For every click in the audio the
first flash onset in the picture is paired with it; the median offset says how late (+) or
early (-) the light is relative to the sound. Needs ffmpeg/ffprobe.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys

import numpy as np
from video_luma import luma_series

SR = 48000


def audio_clicks(path: str) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le",
           "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.abs(np.frombuffer(raw, dtype=np.float32))
    if len(x) < SR:
        raise SystemExit("no audio track found in the video")
    k = int(0.004 * SR)  # 4 ms smoothing
    env = np.convolve(x, np.ones(k) / k, mode="same")
    thr = 0.35 * np.percentile(env, 99.8)
    clicks, last = [], -1.0
    above = np.flatnonzero(env > thr)
    for i in above:
        t = i / SR
        if t - last > 0.15:  # refractory
            clicks.append(t)
        last = t
    return np.array(clicks)


def flash_onsets(path: str, roi: str | None) -> tuple[np.ndarray, float]:
    box = None
    if roi:  # "w:h:x:y" in source pixels
        w, h, x, yy = (int(v) for v in roi.split(":"))
        box = (w, h, x, yy)
    t, y = luma_series(path, box)
    span = max(y) - min(y)
    fps = (len(t) - 1) / (t[-1] - t[0])
    onsets, prev_i = [], -10**9
    for i in range(1, len(y)):
        if y[i] - y[i - 1] >= 0.25 * span and i - prev_i > max(2, int(0.1 * fps)):
            onsets.append(t[i])
            prev_i = i
    return np.array(onsets), fps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--lead", type=float, default=30.0, help="beat_lead_ms used during the clip")
    ap.add_argument("--roi", help="crop w:h:x:y (source pixels) to the lit area")
    ap.add_argument("--skip", type=float, default=15.0, help="ignore the first seconds (lock-in)")
    ap.add_argument("--min-lag", type=float, default=-0.15, help="earliest accepted flash (s)")
    ap.add_argument("--max-lag", type=float, default=0.35, help="latest accepted flash (s)")
    args = ap.parse_args()

    clicks = audio_clicks(args.video)
    flashes, fps = flash_onsets(args.video, args.roi)
    clicks = clicks[clicks > args.skip]
    print(f"{len(clicks)} clicks (after {args.skip:g} s), {len(flashes)} flash onsets, "
          f"{fps:.0f} fps (frame = {1000 / fps:.1f} ms)")
    lags = []
    for c in clicks:
        cand = flashes[(flashes >= c + args.min_lag) & (flashes <= c + args.max_lag)]
        if len(cand):
            lags.append(float(cand[np.argmin(np.abs(cand - c))] - c) * 1000)
    if len(lags) < 3:
        print("not enough click/flash pairs; check --roi, exposure and that the show was in the "
              "beat scene", file=sys.stderr)
        return 1
    med = statistics.median(lags)
    q = np.percentile(lags, [10, 90])
    print(f"paired {len(lags)}/{len(clicks)} clicks: flash minus click = median {med:+.0f} ms "
          f"(p10 {q[0]:+.0f}, p90 {q[1]:+.0f}, spread {q[1] - q[0]:.0f} ms)")
    print("  (+ = light late, - = light early; the light is meant to be ~0)")
    period_ms = float(np.median(np.diff(clicks))) * 1000 if len(clicks) > 2 else 0.0
    if period_ms and abs(med) > 0.25 * period_ms:
        alias = med - period_ms * np.sign(med)
        print(f"  WARNING: readings repeat every click period ({period_ms:.0f} ms), so "
              f"{med:+.0f} ms could also be {alias:+.0f} ms. Confirm with a second click tempo "
              f"(e.g. 120 vs 150 BPM) before changing any setting.")
    suggested = args.lead + med
    print(f"  suggested beat_lead_ms: {suggested:.0f}  (was {args.lead:g}; frame-quantised "
          f"clips are good to about +-{1000 / fps:.0f} ms, plus speaker/room sound delay of "
          f"~{3:d} ms per metre)")
    if suggested < 0:
        print(f"  the sound arrives late (Bluetooth speaker?): keep beat_lead_ms at its wired "
              f"value and set show.light_delay_ms to about {-suggested + args.lead:.0f} instead "
              f"(that also delays kick-triggered flashes, which a lead cannot do)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
