"""Sources backed by sample arrays: audio files (via ffmpeg), synthetic test signals."""

from __future__ import annotations

import subprocess
import time

import numpy as np

from dmxreactive.audio.base import AudioStats


class ArraySource:
    """Plays a mono float32 array block by block, optionally paced in real time."""

    def __init__(self, samples: np.ndarray, sample_rate: int = 48000, block_size: int = 1024,
                 realtime: bool = True, loop: bool = False) -> None:
        self._samples = np.asarray(samples, dtype=np.float32)
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.stats = AudioStats()
        self._realtime = realtime
        self._loop = loop
        self._pos = 0
        self._t0 = 0.0

    def start(self) -> None:
        self._pos = 0
        self._t0 = time.perf_counter()

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        end = self._pos + self.block_size
        if end > len(self._samples):
            if not self._loop:
                raise EOFError("end of audio")
            self._pos, end = 0, self.block_size
            self._t0 = time.perf_counter()
        if self._realtime:  # a block is "available" once its last sample has been played
            wait = self._t0 + end / self.sample_rate - time.perf_counter()
            if wait > timeout:
                time.sleep(timeout)
                return None
            if wait > 0:
                time.sleep(wait)
        block = self._samples[self._pos:end]
        self._pos = end
        self.stats.blocks += 1
        return block

    def stop(self) -> None:
        pass


def decode_file(path: str, sample_rate: int = 48000) -> np.ndarray:
    """Decode any audio/video file to mono float32 using ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(sample_rate),
           "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise OSError(f"ffmpeg could not decode {path}: {proc.stderr.decode(errors='replace')}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def FileSource(path: str, sample_rate: int = 48000, block_size: int = 1024,  # noqa: N802
               realtime: bool = True, loop: bool = False) -> ArraySource:
    return ArraySource(decode_file(path, sample_rate), sample_rate, block_size, realtime, loop)


def click_track(bpm: float, seconds: float, sample_rate: int = 48000, click_ms: float = 8.0,
                freq: float = 100.0) -> np.ndarray:
    """A kick-like decaying low sine at every beat; the reference signal for beat tests."""
    n = int(seconds * sample_rate)
    out = np.zeros(n, dtype=np.float32)
    length = int(click_ms / 1000 * sample_rate * 4)
    t = np.arange(length) / sample_rate
    click = (np.sin(2 * np.pi * freq * t) * np.exp(-t / (click_ms / 1000))).astype(np.float32)
    step = 60.0 / bpm * sample_rate
    pos = 0.0
    while int(pos) < n:
        i = int(pos)
        seg = click[: n - i]
        out[i:i + len(seg)] += seg
        pos += step
    return out


def sine(freq: float, seconds: float, sample_rate: int = 48000, amplitude: float = 0.5
         ) -> np.ndarray:
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)
