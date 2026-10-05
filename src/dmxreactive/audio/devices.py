"""PulseAudio/PipeWire device discovery through `pactl`."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str
    state: str
    is_monitor: bool


def _pactl(*args: str) -> str:
    return subprocess.run(["pactl", *args], capture_output=True, text=True, check=True,
                          timeout=5).stdout


def list_sources() -> list[Source]:
    out = []
    for line in _pactl("list", "short", "sources").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out.append(Source(parts[1], parts[-1], parts[1].endswith(".monitor")))
    return out


def default_sink() -> str | None:
    try:
        return _pactl("get-default-sink").strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
