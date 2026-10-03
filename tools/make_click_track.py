"""Write a click-track WAV for latency measurement.

    python tools/make_click_track.py out.wav [bpm] [seconds]

Play it on the laptop (e.g. `paplay out.wav`) while `dmxreactive run --scene beat` is running and
film lights + speaker. Clicks are 100 Hz kick-like thumps (the analyser's clearest case)."""

import sys
import wave

import numpy as np

from dmxreactive.audio.array_source import click_track

out = sys.argv[1] if len(sys.argv) > 1 else "support/click150.wav"
bpm = float(sys.argv[2]) if len(sys.argv) > 2 else 150.0
seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 40.0
x = np.clip(click_track(bpm, seconds, 48000) * 0.8, -1, 1)
with wave.open(out, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(48000)
    w.writeframes((x * 32767).astype("<i2").tobytes())
print(f"wrote {out}: {bpm:g} BPM, {seconds:g} s")
