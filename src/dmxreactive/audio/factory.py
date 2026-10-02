"""Build the configured audio source."""

from __future__ import annotations

from dmxreactive.audio.array_source import FileSource
from dmxreactive.audio.base import AudioSource
from dmxreactive.audio.pulse import PulseMonitorSource
from dmxreactive.config import AudioConfig


def create_source(cfg: AudioConfig, file: str | None = None, device: str | None = None,
                  loop: bool = False) -> AudioSource:
    """A file replayed in real time if `file` is given, else live system-output capture."""
    if file:
        return FileSource(file, cfg.sample_rate, cfg.block_size, realtime=True, loop=loop)
    return PulseMonitorSource(device or cfg.device, cfg.sample_rate, cfg.block_size,
                              cfg.latency_ms)
