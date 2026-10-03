"""Load config.yaml into a validated Config (node settings, rate, patched fixtures)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from dmxreactive.dmx.artnet import ARTNET_PORT
from dmxreactive.dmx.profiles import Fixture, FixtureProfile, Patch, ProfileError
from dmxreactive.engine.show import ShowConfig

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
    show: ShowConfig = ShowConfig()


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
    sh = raw.get("show", {}) or {}
    d = ShowConfig()

    def pair(key: str, default: tuple[float, float]) -> tuple[float, float]:
        lo, hi = (float(v) for v in sh.get(key, default))
        if not 0.0 <= lo < hi <= 1.0:
            raise ProfileError(f"show.{key} must be [lo, hi] with 0 <= lo < hi <= 1")
        return lo, hi

    show = ShowConfig(
        beat_lead_ms=float(sh.get("beat_lead_ms", d.beat_lead_ms)),
        max_flash_hz=float(sh.get("max_flash_hz", d.max_flash_hz)),
        flash_decay_ms=float(sh.get("flash_decay_ms", d.flash_decay_ms)),
        master=float(sh.get("master", d.master)),
        pan_limits=pair("pan_limits", d.pan_limits),
        tilt_limits=pair("tilt_limits", d.tilt_limits),
        scene=str(sh.get("scene", d.scene)),
        colour_hold_s=float(sh.get("colour_hold_s", d.colour_hold_s)),
        silence_fade_s=float(sh.get("silence_fade_s", d.silence_fade_s)),
        flash_gate=float(sh.get("flash_gate", d.flash_gate)),
        kick_flash_strength=float(sh.get("kick_flash_strength", d.kick_flash_strength)),
        ambient_flash_rise_db=float(sh.get("ambient_flash_rise_db", d.ambient_flash_rise_db)),
        ambient_flash_energy=float(sh.get("ambient_flash_energy", d.ambient_flash_energy)),
        ambient_flash_hz=float(sh.get("ambient_flash_hz", d.ambient_flash_hz)),
        drive_kick_rate=float(sh.get("drive_kick_rate", d.drive_kick_rate)),
        drive_energy=float(sh.get("drive_energy", d.drive_energy)),
        drive_flash_gate=float(sh.get("drive_flash_gate", d.drive_flash_gate)),
        drive_min_confidence=float(sh.get("drive_min_confidence", d.drive_min_confidence)),
        mover_flash_every=int(sh.get("mover_flash_every", d.mover_flash_every)),
        mover_flash_hold_ms=float(sh.get("mover_flash_hold_ms", d.mover_flash_hold_ms)),
        drop_duration_s=float(sh.get("drop_duration_s", d.drop_duration_s)),
        drop_dip=float(sh.get("drop_dip", d.drop_dip)),
        drop_min_gap_s=float(sh.get("drop_min_gap_s", d.drop_min_gap_s)),
        breath_period_s=float(sh.get("breath_period_s", d.breath_period_s)),
        breath_depth=float(sh.get("breath_depth", d.breath_depth)),
    )
    if not 0.0 < show.max_flash_hz <= 10.0:
        raise ProfileError("show.max_flash_hz must be in (0, 10]")
    if not 0.2 <= show.drop_duration_s <= 4.0:
        raise ProfileError("show.drop_duration_s must be between 0.2 and 4 seconds")
    if not 0.0 <= show.breath_depth <= 1.0 or show.breath_period_s < 1.0:
        raise ProfileError("show.breath_depth must be 0..1 and breath_period_s >= 1")
    if show.scene not in ("auto", "beat", "ambient", "drive"):
        raise ProfileError("show.scene must be auto, beat, ambient or drive")
    return Config(node=node, rate_hz=rate, patch=Patch(fixtures), audio=audio, show=show)
