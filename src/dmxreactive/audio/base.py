"""Audio source interface. Sources deliver fixed-size mono float32 blocks in -1..1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class AudioStats:
    blocks: int = 0
    dropped: int = 0  # blocks discarded because the consumer fell behind (keeps latency low)
    restarts: int = 0  # times the capture backend had to be reopened


class AudioSource(Protocol):
    sample_rate: int
    block_size: int
    stats: AudioStats

    def start(self) -> None: ...

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        """Next block (float32, shape (block_size,)), or None if nothing arrived in `timeout`.

        Raises EOFError when a finite source (file) is exhausted.
        """
        ...

    def stop(self) -> None: ...
