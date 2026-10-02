"""Synthetic ground-truth check: click tracks at known tempi -> detected BPM and beat timing."""

import numpy as np

from dmxreactive.analysis.analyzer import Analyzer
from dmxreactive.audio.array_source import click_track

SR, BLOCK = 48000, 1024
for bpm in (90, 100, 120, 128, 140, 150, 160, 174, 190):
    x = click_track(bpm, 40.0, SR)
    an = Analyzer(SR, BLOCK)
    feats = [an.process(x[i:i + BLOCK]) for i in range(0, len(x) - BLOCK + 1, BLOCK)]
    beats = np.array([f.t for f in feats if f.beat and f.t > 15])
    kicks = np.array([f.t for f in feats if f.kick and f.t > 15])
    true = np.arange(0, 40, 60 / bpm)
    d_beat = np.array([b - true[np.argmin(abs(true - b))] for b in beats]) * 1000
    d_kick = np.array([k - true[np.argmin(abs(true - k))] for k in kicks]) * 1000
    final = [f.bpm for f in feats[-50:]]
    def stat(d):
        if not len(d):
            return "none"
        return f"{np.median(d):+5.1f} ms (range {d.min():+.0f}..{d.max():+.0f}, n={len(d)})"

    print(f"{bpm:4d} BPM -> {np.median(final):6.1f}  conf {feats[-1].beat_confidence:.2f}  "
          f"beat {stat(d_beat)}  kick {stat(d_kick)}")
