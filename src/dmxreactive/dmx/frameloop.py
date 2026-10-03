"""Fixed-rate loop with absolute deadlines (no cumulative drift) and jitter statistics."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

_SPIN_S = 0.0015  # sleep until this close to the deadline, then busy-wait for precision


ERROR_WINDOW = 4000  # keep the last N tick errors (~100 s at 40 Hz): bounded memory for days


@dataclass
class LoopStats:
    ticks: int = 0
    late_ticks: int = 0  # ticks that started more than one full period late
    # start time minus deadline, seconds, of the most recent ticks only
    errors: deque[float] = field(default_factory=lambda: deque(maxlen=ERROR_WINDOW))
    max_error_s: float = 0.0  # worst tick since the start

    def summary(self) -> dict[str, float]:
        if not self.errors:
            return {"ticks": 0, "mean_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
        e = sorted(abs(x) * 1000 for x in self.errors)
        return {
            "ticks": self.ticks,
            "mean_ms": sum(e) / len(e),
            "p95_ms": e[int(0.95 * (len(e) - 1))],
            "max_ms": max(e[-1], self.max_error_s * 1000),
        }


class FrameLoop:
    def __init__(self, rate_hz: float, tick: Callable[[float], None]) -> None:
        self.period = 1.0 / rate_hz
        self._tick = tick
        self.stop_event = threading.Event()
        self.stats = LoopStats()

    def run(self, duration_s: float | None = None) -> None:
        start = time.perf_counter()
        deadline = start
        end = None if duration_s is None else start + duration_s
        while not self.stop_event.is_set():
            now = time.perf_counter()
            if end is not None and now >= end:
                break
            remaining = deadline - now
            if remaining > _SPIN_S:
                self.stop_event.wait(remaining - _SPIN_S)
                continue
            while time.perf_counter() < deadline:
                pass
            t = time.perf_counter()
            self.stats.errors.append(t - deadline)
            self.stats.max_error_s = max(self.stats.max_error_s, abs(t - deadline))
            self._tick(t - start)
            self.stats.ticks += 1
            deadline += self.period
            if time.perf_counter() > deadline + self.period:  # fell behind: skip, don't burst
                self.stats.late_ticks += 1
                deadline = time.perf_counter() + self.period
