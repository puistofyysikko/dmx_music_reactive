"""Cache per-block analysis features of tracks for the offline pre-drop evaluation.

    python tools/predrop_features.py out_dir track1 [track2 ...]

Writes <out_dir>/<track stem>.npz with t, bass_energy, bpm, beat, bar_beat, bar_confidence,
beat_locked per analysis block (the inputs of `tools/predrop_eval.py`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from dmxreactive.analysis.analyzer import Analyzer
from dmxreactive.audio.array_source import decode_file

SR, BLOCK = 48000, 1024


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for path in sys.argv[2:]:
        x = decode_file(path, SR)
        an = Analyzer(SR, BLOCK)
        rows = []
        for i in range(0, len(x) - BLOCK + 1, BLOCK):
            f = an.process(x[i:i + BLOCK])
            rows.append((f.t, f.bass_energy, f.bpm, float(f.beat), f.bar_beat, f.bar_confidence,
                         float(f.beat_locked), float(f.silent)))
        np.savez(out / (Path(path).stem + ".npz"), cols=np.array(rows),
                 names=np.array("t bass bpm beat bar_beat bar_conf locked silent".split()))
        print(f"{Path(path).name}: {len(rows)} blocks, {rows[-1][0]:.0f} s", flush=True)


if __name__ == "__main__":
    main()
