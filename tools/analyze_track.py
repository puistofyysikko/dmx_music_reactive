"""Run the real-time analyser over an audio file and report/plot what it finds.

    python tools/analyze_track.py support/tracks/song.mp3 [--plot out.png] [--reference]

--reference also runs librosa (dev extra) and compares beats: F-measure (+-70 ms) after
removing the best constant offset, since the real-time grid is predicted, not detected.
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from dmxreactive.analysis.analyzer import Analyzer
from dmxreactive.analysis.features import BAND_NAMES
from dmxreactive.audio.array_source import decode_file

SR, BLOCK = 48000, 1024


def run(path: str):
    x = decode_file(path, SR)
    an = Analyzer(SR, BLOCK)
    feats = []
    t0 = time.perf_counter()
    for i in range(0, len(x) - BLOCK + 1, BLOCK):
        feats.append(an.process(x[i:i + BLOCK]))
    cpu = time.perf_counter() - t0
    return x, feats, cpu


def f_measure(est: np.ndarray, ref: np.ndarray, tol: float = 0.07) -> tuple[float, float]:
    """Best F1 over constant offsets of `est` (seconds); returns (f1, offset)."""
    best = (0.0, 0.0)
    for off in np.arange(-0.15, 0.1501, 0.005):
        e = est + off
        hit_ref = sum(np.min(np.abs(e - r)) <= tol for r in ref) if len(e) else 0
        hit_est = sum(np.min(np.abs(ref - b)) <= tol for b in e) if len(ref) else 0
        p, r = hit_est / max(len(e), 1), hit_ref / max(len(ref), 1)
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        if f1 > best[0]:
            best = (f1, float(off))
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--plot")
    ap.add_argument("--reference", action="store_true")
    args = ap.parse_args()

    x, feats, cpu = run(args.path)
    dur = len(x) / SR
    t = np.array([f.t for f in feats])
    beats = np.array([f.t for f in feats if f.beat])
    kicks = np.array([f.t for f in feats if f.kick])
    bpm = np.array([f.bpm for f in feats])
    conf = np.array([f.beat_confidence for f in feats])
    print(f"{args.path}\n  {dur:.0f} s audio analysed in {cpu:.1f} s "
          f"({cpu / dur * 100:.1f} % of one core, {cpu / len(feats) * 1000:.2f} ms/block)")
    print(f"  kicks {len(kicks)} ({len(kicks) / dur:.2f}/s)  beats {len(beats)}  "
          f"hats {feats[-1].hat_count}")
    for a in range(0, int(dur), 20):
        m = (t >= a) & (t < a + 20)
        if m.any():
            print(f"  {a:3d}-{a + 20:3d}s  bpm median {np.median(bpm[m]):6.1f}  "
                  f"conf {conf[m].mean():.2f}  "
                  f"kicks/s {np.sum((kicks >= a) & (kicks < a + 20)) / 20:.2f}")
    late = beats[beats > 12]
    if len(late) > 3 and len(kicks):
        d = np.array([np.min(np.abs(kicks - b)) for b in late]) * 1000
        print(f"  beat-vs-nearest-kick distance: median {np.median(d):.0f} ms, "
              f"{np.mean(d < 60) * 100:.0f} % within 60 ms")
    if args.reference:
        import librosa
        y, sr = librosa.load(args.path, sr=22050, mono=True)
        tempo, ref = librosa.beat.beat_track(y=y, sr=sr, units="time", start_bpm=140)
        f1, off = f_measure(late, ref[ref > 12])
        print(f"  librosa tempo {float(np.atleast_1d(tempo)[0]):.1f}; beat F1 vs librosa "
              f"{f1:.2f} (offset {off * 1000:+.0f} ms)")
    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(5, 1, figsize=(15, 11), sharex=True)
        ax[0].plot(t, [f.level for f in feats], lw=0.8)
        ax[0].set_ylabel("level")
        for i, name in enumerate(BAND_NAMES):
            ax[1].plot(t, [f.bands[i] for f in feats], lw=0.6, label=name)
        ax[1].legend(ncol=5, fontsize=7)
        ax[1].set_ylabel("bands")
        ax[2].plot(t, [f.flux for f in feats], lw=0.6)
        ax[2].vlines(kicks, 0, 1, colors="r", lw=0.6, label="kick")
        ax[2].vlines(beats, 0, 0.5, colors="g", lw=1.0, label="beat")
        ax[2].legend(fontsize=7)
        ax[2].set_ylabel("flux")
        ax[3].plot(t, bpm)
        ax[3].set_ylabel("bpm")
        ax[4].plot(t, conf)
        ax[4].plot(t, [f.energy_trend for f in feats], lw=0.6)
        ax[4].set_ylabel("conf/trend")
        ax[4].set_xlabel("s")
        fig.tight_layout()
        fig.savefig(args.plot, dpi=70)
        print(f"  plot: {args.plot}")


if __name__ == "__main__":
    main()
