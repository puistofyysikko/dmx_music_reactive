"""Load config.yaml into a validated Config (node settings, rate, patched fixtures)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from dmxreactive.dmx.artnet import ARTNET_PORT
from dmxreactive.dmx.profiles import Fixture, FixtureProfile, Patch, ProfileError

DEFAULT_CONFIG = Path("config/config.yaml")
EXAMPLE_CONFIG = Path("config/config.example.yaml")


@dataclass(frozen=True)
class NodeConfig:
    host: str
    port: int
    port_address: int  # 15-bit Art-Net address: net<<8 | subnet<<4 | universe


@dataclass(frozen=True)
class AudioConfig:
    device: str = "@DEFAULT_MONITOR@"  # PipeWire/Pulse source; default = monitor of the output
    sample_rate: int = 48000
    block_size: int = 1024  # samples per block (1024 @ 48 kHz = 21 ms)
    latency_ms: int = 20  # capture buffer requested from the sound server


@dataclass(frozen=True)
class Config:
    node: NodeConfig
    rate_hz: float
    patch: Patch
    audio: AudioConfig = AudioConfig()


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else EXAMPLE_CONFIG)
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    try:
        node_raw = raw["node"]
        net, subnet, universe = (int(node_raw.get(k, 0)) for k in ("net", "subnet", "universe"))
        if not (0 <= net < 128 and 0 <= subnet < 16 and 0 <= universe < 16):
            raise ProfileError("node net/subnet/universe out of range (127/15/15)")
        node = NodeConfig(
            host=str(node_raw["host"]),
            port=int(node_raw.get("port", ARTNET_PORT)),
            port_address=(net << 8) | (subnet << 4) | universe,
        )
        rate = float(raw.get("output", {}).get("rate_hz", 30))
        if not 1 <= rate <= 44:
            raise ProfileError("output.rate_hz must be between 1 and 44")
        profile_dir = path.parent / "fixtures"
        profiles: dict[str, FixtureProfile] = {}
        fixtures = []
        for f in raw["fixtures"]:
            pid = f["profile"]
            if pid not in profiles:
                profiles[pid] = FixtureProfile.load(profile_dir / f"{pid}.json")
            fixtures.append(
                Fixture(profiles[pid], int(f["address"]), f["name"], f.get("group", ""))
            )
    except KeyError as exc:
        raise ProfileError(f"{path}: missing key {exc}") from exc
    a = raw.get("audio", {}) or {}
    defaults = AudioConfig()
    audio = AudioConfig(
        device=str(a.get("device", defaults.device)),
        sample_rate=int(a.get("sample_rate", defaults.sample_rate)),
        block_size=int(a.get("block_size", defaults.block_size)),
        latency_ms=int(a.get("latency_ms", defaults.latency_ms)),
    )
    if audio.block_size < 256 or audio.sample_rate < 8000:
        raise ProfileError("audio.block_size must be >= 256 and sample_rate >= 8000")
    return Config(node=node, rate_hz=rate, patch=Patch(fixtures), audio=audio)
