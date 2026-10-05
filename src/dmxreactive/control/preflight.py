"""Pre-flight checklist: everything that can be verified from the laptop before a show."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass

from dmxreactive.audio.devices import default_sink, list_sources
from dmxreactive.config import Config
from dmxreactive.dmx.artnet import NodeInfo, discover

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str
    hint: str = ""


def check_config(config: Config) -> Check:
    names = ", ".join(f"{f.name}@{f.address}" for f in config.patch.fixtures)
    return Check(OK, "config", f"{len(config.patch.fixtures)} fixtures ({names}), "
                 f"{config.rate_hz:g} Hz")


def local_addresses() -> list[tuple[str, str]]:
    """[(interface, ipv4/prefix)] from `ip -4 -o addr`."""
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [(m[1], m[2]) for m in re.finditer(r"^\d+:\s+(\S+)\s+inet\s+(\S+)", out, re.M)]


def _same_subnet(ip: str, cidr: str) -> bool:
    import ipaddress

    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def check_network(config: Config, addresses: list[tuple[str, str]] | None = None) -> Check:
    host = config.node.host
    if host.startswith("127."):
        return Check(OK, "network", f"target {host} (loopback, simulator)")
    for iface, cidr in (addresses if addresses is not None else local_addresses()):
        if _same_subnet(host, cidr):
            return Check(OK, "network", f"{iface} {cidr} reaches {host}")
    return Check(FAIL, "network", f"no interface is on the same subnet as the node ({host})",
                 "plug in the USB-Ethernet dongle and run scripts/setup_dongle.sh")


def check_node(config: Config, info: NodeInfo | None, error: str = "") -> Check:
    host = config.node.host
    if error:
        return Check(WARN, "node", f"could not poll the node: {error}",
                     "is QLC+ (or another Art-Net program) running? close it and retry")
    if info is None:
        return Check(FAIL, "node", f"{host} did not answer an ArtPoll",
                     "node powered (USB-C)? Ethernet cable plugged in? run `ping " + host + "`")
    wanted = config.node.port_address
    ports = info.port_addresses()
    if wanted not in ports:
        shown = ", ".join(str(p) for p in ports)
        return Check(FAIL, "node", f"'{info.short_name}' answers, but its output universe is "
                     f"{shown} and the config sends to {wanted}",
                     "set node.net/subnet/universe in config.yaml to match the node's web page")
    return Check(OK, "node", f"'{info.short_name}' at {info.ip}, output universe matches "
                 f"({wanted})")


def check_audio(config: Config, capture_s: float = 1.5) -> list[Check]:
    from dmxreactive.audio.level import rms_dbfs
    from dmxreactive.audio.pulse import PulseMonitorSource

    if shutil.which("pactl") is None or shutil.which("parec") is None:
        return [Check(FAIL, "audio", "pactl/parec not found", "dnf install pulseaudio-utils")]
    sink = default_sink()
    if not sink:
        return [Check(FAIL, "audio", "no default output device",
                      "is the sound server running? (pipewire-pulse)")]
    out = [Check(OK, "audio output", sink)]
    if sink.startswith("bluez") and config.show.light_delay_ms < 100:
        out.append(Check(
            WARN, "bluetooth", "the default output is a Bluetooth speaker: its sound arrives "
            "~150-250 ms late, so the lights will run ahead of it",
            "measure with tools/latency_analyze.py, then set show.light_delay_ms (~150-250) "
            "in config.yaml; wired/USB output needs 0"))
    device = config.audio.device
    if device == "@DEFAULT_MONITOR@" and f"{sink}.monitor" not in {s.name for s in list_sources()}:
        return out + [Check(FAIL, "audio capture", f"no monitor source for {sink}")]
    src = PulseMonitorSource(device, config.audio.sample_rate, config.audio.block_size,
                             config.audio.latency_ms)
    src.start()
    levels, end = [], time.monotonic() + capture_s
    try:
        while time.monotonic() < end:
            block = src.read(0.3)
            if block is not None:
                levels.append(rms_dbfs(block))
    finally:
        src.stop()
    if not levels:
        return out + [Check(FAIL, "audio capture", "no audio blocks arrived",
                            "check audio.device in config.yaml and `dmxreactive list-devices`")]
    peak = max(levels)
    if peak < -70:
        return out + [Check(WARN, "audio capture",
                            f"works ({len(levels)} blocks) but silent right now",
                            "start some music to see the level move")]
    return out + [Check(OK, "audio capture", f"{len(levels)} blocks, level up to {peak:.0f} dBFS")]


def run_preflight(config: Config, audio: bool = True) -> list[Check]:
    checks = [check_config(config), check_network(config)]
    info, err = None, ""
    try:
        info = discover(config.node.host, config.node.port)
    except OSError as exc:
        err = str(exc)
    checks.append(check_node(config, info, err))
    if audio:
        checks.extend(check_audio(config))
    return checks


def format_checks(checks: list[Check]) -> str:
    mark = {OK: "✔", WARN: "!", FAIL: "✘"}
    lines = []
    for c in checks:
        lines.append(f" {mark[c.status]} {c.name:<15} {c.detail}")
        if c.hint and c.status != OK:
            lines.append(f"     → {c.hint}")
    return "\n".join(lines)


def ok(checks: list[Check]) -> bool:
    return all(c.status != FAIL for c in checks)
