"""System-output capture on Linux: the monitor source of the sound server (PipeWire/PulseAudio).

Implemented with a `parec` subprocess instead of PortAudio: no system library or Python audio
wheel is needed, any app's playback is captured, and the sound server does the downmix/resample.
The backend is re-opened if parec dies, and when the default output device changes (e.g. the
speakers are swapped for a USB DAC feeding the mixer) so lights keep following the music.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time

import numpy as np

from dmxreactive.audio.base import AudioStats
from dmxreactive.audio.devices import default_sink

log = logging.getLogger(__name__)

DEFAULT_MONITOR = "@DEFAULT_MONITOR@"
_POLL_SINK_S = 2.0
_MAX_BACKOFF_S = 5.0


class PulseMonitorSource:
    def __init__(self, device: str = DEFAULT_MONITOR, sample_rate: int = 48000,
                 block_size: int = 1024, latency_ms: int = 20, queue_blocks: int = 8,
                 command: list[str] | None = None) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.stats = AudioStats()
        self._latency_ms = latency_ms
        self._command = command  # override for tests
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=queue_blocks)
        self._stop = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None
        self._proc_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="audio-capture", daemon=True)
        self._watcher = threading.Thread(target=self._watch_default_sink, name="audio-sink-watch",
                                         daemon=True)

    def _build_command(self) -> list[str]:
        if self._command is not None:
            return self._command
        return ["parec", "--raw", "--format=float32le", f"--rate={self.sample_rate}",
                "--channels=1", f"--device={self.device}", f"--latency-msec={self._latency_ms}",
                "--client-name=dmxreactive", "--stream-name=lights"]

    def start(self) -> None:
        self._thread.start()
        if self.device == DEFAULT_MONITOR and self._command is None:
            self._watcher.start()

    def stop(self) -> None:
        self._stop.set()
        self._terminate()
        self._thread.join(timeout=2)

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _terminate(self) -> None:
        with self._proc_lock:
            proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    def _push(self, block: np.ndarray) -> None:
        try:
            self._queue.put_nowait(block)
        except queue.Full:  # consumer is behind: drop the oldest so latency stays bounded
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(block)
            self.stats.dropped += 1
        self.stats.blocks += 1

    def _run(self) -> None:
        nbytes = self.block_size * 4
        backoff = 0.5
        while not self._stop.is_set():
            try:
                proc = subprocess.Popen(self._build_command(), stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0)
            except OSError as exc:
                log.error("cannot start audio capture: %s", exc)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_S)
                continue
            with self._proc_lock:
                self._proc = proc
            got_data = False
            assert proc.stdout is not None
            while not self._stop.is_set():
                data = _read_exact(proc.stdout, nbytes)
                if data is None:
                    break
                got_data = True
                backoff = 0.5
                self._push(np.frombuffer(data, dtype=np.float32).copy())
            proc.kill()
            proc.wait()
            if self._stop.is_set():
                return
            self.stats.restarts += 1
            log.warning("audio capture ended (%s); reopening in %.1fs",
                        "stream closed" if got_data else "no data", backoff)
            self._stop.wait(backoff)
            if not got_data:
                backoff = min(backoff * 2, _MAX_BACKOFF_S)

    def _watch_default_sink(self) -> None:
        current = default_sink()
        while not self._stop.wait(_POLL_SINK_S):
            new = default_sink()
            if new and current and new != current:
                log.info("default output changed (%s -> %s); reopening capture", current, new)
                self._terminate()
            current = new or current


def _read_exact(stream, n: int) -> bytes | None:
    """Read exactly n bytes from an unbuffered pipe; None on EOF."""
    chunks, remaining = [], n
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def wait_for_audio(source, seconds: float) -> bool:
    """True if the source delivers at least one block within `seconds`."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if source.read(0.1) is not None:
            return True
    return False
