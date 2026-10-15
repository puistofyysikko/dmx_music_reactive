"""Live runtime: audio capture -> analysis thread -> show (30 Hz) -> Art-Net.

Threads: audio capture (inside the source), analysis (this module), keyboard, and the render
loop on the calling thread. The analysis thread publishes the newest `Features` (an immutable
snapshot) with a monotonic timestamp; the render loop reads it, so neither ever blocks the other.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from dmxreactive.analysis.analyzer import Analyzer
from dmxreactive.analysis.features import Features
from dmxreactive.app import run_output
from dmxreactive.audio.base import AudioSource
from dmxreactive.config import Config
from dmxreactive.control.keys import KeyReader
from dmxreactive.dmx.artnet import ArtNetSender
from dmxreactive.dmx.frameloop import FrameLoop
from dmxreactive.engine.show import Show

log = logging.getLogger(__name__)


class FeatureHolder:
    """Newest `Features` from the analysis thread, optionally delayed by `delay_s` (for
    outputs whose sound arrives late, e.g. Bluetooth speakers: the lights wait for it)."""

    def __init__(self, delay_s: float = 0.0) -> None:
        self._lock = threading.Lock()
        self._delay = delay_s
        self._hist: deque[tuple[float, Features]] = deque()

    def set(self, f: Features) -> None:
        now = time.monotonic()
        with self._lock:
            self._hist.append((now, f))
            cutoff = now - self._delay - 1.0
            while len(self._hist) > 1 and self._hist[0][0] < cutoff:
                self._hist.popleft()

    def get(self) -> tuple[Features | None, float]:
        """(newest features at least `delay` old, their age in seconds)."""
        with self._lock:
            now = time.monotonic()
            chosen: tuple[float, Features] | None = None
            for at, f in self._hist:
                if at <= now - self._delay:
                    chosen = (at, f)
                else:
                    break
            if chosen is None:
                return None, 0.0
            return chosen[1], now - chosen[0]


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


@dataclass
class LiveState:
    """Everything the status display needs, filled in by `run_live` (read-only for the UI)."""

    config: Config
    show: Show
    holder: FeatureHolder
    source: AudioSource
    analysis: AnalysisThread
    target: str = ""
    keys_active: bool = False
    loop: FrameLoop | None = None
    sender: ArtNetSender | None = None
    started: float = field(default_factory=time.monotonic)
    _pk: tuple[float, int] = (0.0, 0)
    packets_per_s: float = 0.0

    def features(self) -> tuple[Features | None, float]:
        return self.holder.get()

    def update_rates(self) -> None:
        now = time.monotonic()
        sent = self.sender.frames_sent if self.sender else 0
        t0, n0 = self._pk
        if now - t0 >= 1.0:
            self.packets_per_s = (sent - n0) / (now - t0) if t0 else 0.0
            self._pk = (now, sent)


def run_live(config: Config, source: AudioSource, host: str | None = None,
             port: int | None = None, duration_s: float | None = None, keys: bool = True,
             status: Callable[[LiveState], None] | None = None, scene: str | None = None,
             on_message: Callable[[str], None] | None = None) -> Show:
    """Run the live show until Ctrl-C/SIGTERM (or `duration_s`). Always ends with blackout.

    `status(state)` is called ~4x per second from the render loop (cheap; do real drawing on
    another thread). `on_message(text)` receives hotkey feedback."""
    analyzer = Analyzer(config.audio.sample_rate, config.audio.block_size)
    delay_s = config.show.light_delay_ms / 1000.0
    holder = FeatureHolder(delay_s)
    show_cfg = config.show if scene is None else _with_scene(config.show, scene)
    show = Show(config.patch, show_cfg)
    analysis = AnalysisThread(source, analyzer, holder)
    reader = KeyReader(lambda ch: _on_key(ch, show, on_message))
    state = LiveState(config, show, holder, source, analysis,
                      target=f"{host or config.node.host}:{port or config.node.port}")
    last = {"t": 0.0}

    def on_tick(_t: float) -> None:
        f, age = holder.get()
        # features are `delay_s` old by design: extrapolate the beat phase to "now - delay"
        show.step(f, max(0.0, age - delay_s), 1.0 / config.rate_hz)
        if status and show.t - last["t"] >= 0.25:
            last["t"] = show.t
            state.keys_active = reader.active
            state.update_rates()
            status(state)

    def on_start(loop: FrameLoop, sender: ArtNetSender) -> None:
        state.loop, state.sender = loop, sender

    source.start()
    analysis.start()
    if keys:
        reader.start()
    try:
        run_output(config, on_tick, duration_s=duration_s, host=host, port=port,
                   on_start=on_start)
    finally:
        reader.stop()
        analysis.stop()
        source.stop()
    return show


def _with_scene(cfg, scene):
    from dataclasses import replace

    return replace(cfg, scene=scene)


def _on_key(ch: str, show: Show, on_message: Callable[[str], None] | None) -> None:
    msg = show.command(ch)
    if msg and on_message:
        on_message(msg)


def status_line(state: LiveState) -> str:
    """One-line plain status (non-terminal output, or `--plain`)."""
    show = state.show
    f, age = state.features()
    if f is None or age > 1.5 + state.config.show.light_delay_ms / 1000.0:
        audio = "no audio"
    else:
        audio = (f"{f.bpm:5.1f} BPM {'LOCKED' if f.beat_locked else 'free  '} "
                 f"level {f.level:3.0%}")
    flags = ("BLACKOUT " if show.blackout else "") + ("" if show.strobe_enabled else "NOFLASH ") + (
        "" if show.mechanics == "full" else f"MECH-{show.mechanics.upper()} ")
    return f"{show.mood:<8} {audio} master {show.master:.0%} {flags}flashes {show.flashes}"
