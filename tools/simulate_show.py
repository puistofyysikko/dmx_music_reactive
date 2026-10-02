"""Simulate the show on an audio file and report/plot what the lights would do.

    python tools/simulate_show.py song.mp3 [--plot out.png] [--seconds 90]
"""

from __future__ import annotations

import argparse

import numpy as np

from dmxreactive.analysis.analyzer import Analyzer
from dmxreactive.audio.array_source import decode_file
from dmxreactive.config import load_config
from dmxreactive.engine.sim import simulate

SR, BLOCK = 48000, 1024


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--plot")
    ap.add_argument("--seconds", type=float, default=0)
    ap.add_argument("--config")
    args = ap.parse_args()
    from dmxreactive.engine.show import Show

    cfg = load_config(args.config)
    x = decode_file(args.path, SR)
    if args.seconds:
        x = x[: int(args.seconds * SR)]
    an = Analyzer(SR, BLOCK)
    feats = [an.process(x[i:i + BLOCK]) for i in range(0, len(x) - BLOCK + 1, BLOCK)]
    show = Show(cfg.patch, cfg.show)
    ticks = simulate(show, feats)
    t = np.array([tk.t for tk in ticks])
    strobe = np.array([tk.dmx["strobe1"][0] for tk in ticks])
    mover_dim = np.array([tk.dmx["mover1"][7] for tk in ticks])
    rising = [i for i in range(1, len(strobe)) if strobe[i] >= 100 > strobe[i - 1]]
    ft = t[rising]
    beat_mood = np.array([tk.mood == "beat" for tk in ticks])
    print(f"{args.path}: {t[-1]:.0f} s")
    print(f"  beat scene {beat_mood.mean() * 100:.0f} % of the time, "
          f"{int(np.sum(np.diff(beat_mood.astype(int)) != 0))} scene changes")
    if len(ft) > 1:
        print(f"  flashes {len(ft)}  (median interval {np.median(np.diff(ft)) * 1000:.0f} ms, "
              f"max rate {1 / np.min(np.diff(ft)):.1f} Hz)")
    else:
        print(f"  flashes {len(ft)}")
    print(f"  mover dimmer mean {mover_dim.mean() / 255:.2f}, "
          f"pan range used {min(tk.dmx['mover1'][0] for tk in ticks) / 255:.2f}-"
          f"{max(tk.dmx['mover1'][0] for tk in ticks) / 255:.2f}")
    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(4, 1, figsize=(15, 8), sharex=True)
        ax[0].plot(t, strobe, lw=0.6)
        ax[0].set_ylabel("strobe dimmer")
        ax[1].plot(t, mover_dim, lw=0.6)
        ax[1].set_ylabel("mover dimmer")
        ax[2].plot(t, [tk.dmx["mover1"][0] for tk in ticks], lw=0.6, label="pan")
        ax[2].plot(t, [tk.dmx["mover1"][1] for tk in ticks], lw=0.6, label="tilt")
        ax[2].legend(fontsize=7)
        ax[3].plot(t, [tk.dmx["mover1"][5] for tk in ticks], lw=0.8)
        ax[3].fill_between(t, 0, 80 * beat_mood, alpha=0.2, label="beat scene")
        ax[3].set_ylabel("colour (wheel DMX)")
        ax[3].legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=65)
        print(f"  plot: {args.plot}")


if __name__ == "__main__":
    main()
