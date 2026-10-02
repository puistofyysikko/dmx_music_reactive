"""Measure strobe flash rates from a phone/camera video of the light.

    python tools/strobe_analyze.py clip.mp4 [--values 2,30,60,90,103] [--roi x:y:w:h]

Needs ffmpeg/ffprobe. Aim the camera at the lit wall (or the fixture), fixed exposure/focus.
Steady-bright stretches (strobe off, dimmer up) split the clip into blocks; each block's
flash onsets are counted and reported in order, matched to --values.
Limit: a camera at N fps resolves flash rates well below N/2 (60 fps -> use values <= ~10 Hz),
and a flash must last at least one frame to be seen.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

JUMP = 0.25  # fraction of the clip's brightness range that counts as a flash onset
STEADY_S = 0.5  # a bright steady stretch this long separates measurements (strobe off)


def frame_luma(path: str, roi: str | None) -> tuple[list[float], list[float]]:
    crop = f"crop={roi}," if roi else ""
    cmd = ["ffprobe", "-v", "error", "-f", "lavfi", "-i",
           f"movie='{path}',{crop}scale=320:-2,signalstats", "-show_entries",
           "frame=pts_time:frame_tags=lavfi.signalstats.YAVG", "-of", "csv=p=0"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    t, y = [], []
    for line in out.splitlines():
        m = re.match(r"([\d.]+),([\d.]+)", line.strip())
        if m:
            t.append(float(m[1]))
            y.append(float(m[2]))
    if len(t) < 10:
        raise SystemExit("no frames decoded (check path / ffmpeg)")
    return t, y


def analyse(t: list[float], y: list[float]) -> list[dict]:
    """Split the clip at steady-bright stretches, then count flash onsets in each block.

    A flash onset is a frame-to-frame brightness jump of at least JUMP of the clip's range.
    Reports the median-interval rate (robust to a missed flash, but quantised to whole frames)
    and the mean rate (first to last onset; more precise when flashes are regular).
    """
    n = len(t)
    fps = (n - 1) / (t[-1] - t[0])
    lo, hi = min(y), max(y)
    span = hi - lo
    bright = lo + 0.75 * span
    min_steady = int(STEADY_S * fps)
    blocks, start, i = [], 0, 0
    while i < n:  # steady-bright runs separate the blocks
        if y[i] > bright:
            j = i
            while j < n and y[j] > bright:
                j += 1
            if j - i >= min_steady:
                blocks.append((start, i))
                start = j
            i = j
        else:
            i += 1
    blocks.append((start, n))
    results = []
    for a, b in blocks:
        onsets: list[int] = []
        for k in range(max(a, 1), b):
            if y[k] - y[k - 1] >= JUMP * span and (not onsets or k - onsets[-1] > 3):
                onsets.append(k)
        if len(onsets) < 2:
            continue
        ts = [t[k] for k in onsets]
        gaps = sorted(q - p for p, q in zip(ts, ts[1:], strict=False))
        median = gaps[len(gaps) // 2]
        results.append({"start": t[a], "end": t[b - 1], "flashes": len(ts), "hz": 1 / median,
                        "hz_mean": (len(ts) - 1) / (ts[-1] - ts[0]),
                        "period_ms": median * 1000, "fps": fps})
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--values", help="DMX values in the order they were played")
    ap.add_argument("--roi", help="crop region w:h:x:y (ffmpeg crop syntax) to ignore the rest")
    args = ap.parse_args()
    t, y = frame_luma(args.video, args.roi)
    res = analyse(t, y)
    values = args.values.split(",") if args.values else []
    print(f"{len(t)} frames, {res[0]['fps'] if res else (len(t)-1)/(t[-1]-t[0]):.1f} fps, "
          f"{len(res)} measured block(s)")
    for i, r in enumerate(res):
        v = values[i] if i < len(values) else "?"
        print(f"block {i + 1}: DMX {v:>4}  t={r['start']:6.1f}-{r['end']:6.1f}s  "
              f"flashes={r['flashes']:3d}  period={r['period_ms']:7.1f} ms  "
              f"median={r['hz']:.2f} Hz  mean={r['hz_mean']:.2f} Hz")
    if values and len(values) != len(res):
        print(f"warning: expected {len(values)} segments, found {len(res)}; "
              "check the --roi, exposure and lighting", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
