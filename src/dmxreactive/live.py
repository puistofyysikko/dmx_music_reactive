"""Live runtime: audio capture -> analysis thread -> show (30 Hz) -> Art-Net.

Threads: audio capture (inside the source), analysis (this module), keyboard, and the render
loop on the calling thread. The analysis thread publishes the newest `Features` (an immutable
snapshot) with a monotonic timestamp; the render loop reads it, so neither ever blocks the other.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from dmxreactive.analysis.analyzer import Analyzer
from dmxreactive.analysis.features import Features
from dmxreactive.app import run_output
from dmxreactive.audio.base import AudioSource
from dmxreactive.config import Config
from dmxreactive.control.keys import KeyReader
from dmxreactive.engine.show import Show

log = logging.getLogger(__name__)


class FeatureHolder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._f: Features | None = None
        self._at = 0.0

    def set(self, f: Features) -> None:
        with self._lock:
            self._f, self._at = f, time.monotonic()

    def get(self) -> tuple[Features | None, float]:
        """(newest features, their age in seconds)."""
        with self._lock:
            return self._f, (time.monotonic() - self._at) if self._f else 0.0


class AnalysisThread(threading.Thread):
    def __init__(self, source: AudioSource, analyzer: Analyzer, holder: FeatureHolder) -> None:
        super().__init__(name="analysis", daemon=True)
        self._source, self._analyzer, self._holder = source, analyzer, holder
        self._stop_event = threading.Event()
        self.frames = 0
        self.errors = 0
        self.finished = False  # a file source ran out

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                block = self._source.read(0.5)
                if block is None:
                    continue
                self._holder.set(self._analyzer.process(block))
                self.frames += 1
            except EOFError:
                self.finished = True
                return
            except Exception:  # keep the lights alive whatever one frame does
                self.errors += 1
                log.exception("analysis error (frame skipped)")

    def stop(self) -> None:
        self._stop_event.set()


def run_live(config: Config, source: AudioSource, host: str | None = None,
             port: int | None = None, duration_s: float | None = None, keys: bool = True,
             status: Callable[[str], None] | None = print, scene: str | None = None) -> Show:
    """Run the live show until Ctrl-C/SIGTERM (or `duration_s`). Always ends with blackout."""
    analyzer = Analyzer(config.audio.sample_rate, config.audio.block_size)
    holder = FeatureHolder()
    show_cfg = config.show if scene is None else _with_scene(config.show, scene)
    show = Show(config.patch, show_cfg)
    reader = KeyReader(lambda ch: _on_key(ch, show, status))
    analysis = AnalysisThread(source, analyzer, holder)
    last = {"t": 0.0, "msg": ""}

    def on_tick(_t: float) -> None:
        f, age = holder.get()
        show.step(f, age, 1.0 / config.rate_hz)
        if status and show.t - last["t"] >= 0.25:
            last["t"] = show.t
            status(_status_line(show, f, age, reader.active))

    source.start()
    analysis.start()
    if keys:
        reader.start()
    try:
        run_output(config, on_tick, duration_s=duration_s, host=host, port=port)
    finally:
        reader.stop()
        analysis.stop()
        source.stop()
        if status:
            status("")
    return show


def _with_scene(cfg, scene):
    from dataclasses import replace

    return replace(cfg, scene=scene)


def _on_key(ch: str, show: Show, status: Callable[[str], None] | None) -> None:
    msg = show.command(ch)
    if msg and status:
        status(f"\n[{msg}]")


def _status_line(show: Show, f: Features | None, age: float, keys: bool) -> str:
    if f is None or age > 1.5:
        audio = "no audio"
    else:
        audio = (f"{f.bpm:5.1f} BPM {'LOCKED' if f.beat_locked else 'free  '} "
                 f"level {f.level:3.0%}")
    flags = ("BLACKOUT " if show.blackout else "") + ("" if show.strobe_enabled else "NOSTROBE ")
    hint = "  [b blackout, s strobe, a/1/2 scene, +/- master, Ctrl-C quit]" if keys else ""
    return f"\r{show.mood:<8} {audio} master {show.master:.0%} {flags}{hint}\033[K"
