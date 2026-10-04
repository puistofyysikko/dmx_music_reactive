"""Ear test for the downbeat detector: render a track with a loud beep on every detected "one"
and a quiet tick on the other beats of each bar.

    python tools/bar_check.py song.mp3 [out.wav] [--min-confidence 0.35]

Listen to the result (e.g. `paplay out.wav`): do the loud beeps land on the first beat of each
bar? Beeps are only added while the detector is confident enough.
"""

from __future__ import annotations

import argparse
import subprocess
import wave
from pathlib import Path

import numpy as np

from dmxreactive.analysis.analyzer import Analyzer

SR, BLOCK = 48000, 1024


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("track")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--min-confidence", type=float, default=0.35)
    args = ap.parse_args()

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", args.track, "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
        capture_output=True, check=True).stdout
    stereo = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()
    mono = stereo.mean(axis=1)
    an = Analyzer(SR, BLOCK)
    one_times, tick_times, conf = [], [], []
    for i in range(0, len(mono) - BLOCK + 1, BLOCK):
        f = an.process(mono[i:i + BLOCK])
        if f.beat and f.bar_beat >= 0 and f.bar_confidence >= args.min_confidence:
            # the grid beat is predicted at the end of this block; the kick lands ~20 ms earlier
            (one_times if f.bar_beat == 0 else tick_times).append(f.t - 0.02)
            conf.append(f.bar_confidence)

    def tone(freq: float, ms: float, amp: float) -> np.ndarray:
        n = int(SR * ms / 1000)
        t = np.arange(n) / SR
        return (amp * np.sin(2 * np.pi * freq * t) * np.hanning(n)).astype(np.float32)

    beep, tick = tone(1500, 90, 0.5), tone(700, 25, 0.12)
    for times, sound in ((one_times, beep), (tick_times, tick)):
        for t in times:
            a = int(t * SR)
            if 0 <= a < len(stereo) - len(sound):
                stereo[a:a + len(sound), :] += sound[:, None]
    out = args.out or str(Path(args.track).with_name("bar_check_" + Path(args.track).stem + ".wav"))
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(stereo, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"{out}: {len(one_times)} ones marked, {len(tick_times)} other beats ticked "
          f"(mean confidence {np.mean(conf) if conf else 0:.2f}, track {len(mono) / SR:.0f} s)")


if __name__ == "__main__":
    main()
