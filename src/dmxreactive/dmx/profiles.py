"""Fixture profiles (JSON) and fixture instances that convert intents to DMX values.

Channel kinds:
  continuous  normalised 0..1 -> dmx_range (optional 16-bit via fine_offset)
  select      named slot -> DMX value (e.g. colour wheel position)
  rate        0 = off, (0..1] -> dmx_range. Hz mapping for set_hz(), one of:
                hz_range [lo_hz, hi_hz]  frequency linear in DMX value (PicoSpot)
                period_ms [p_lo, p_hi]   flash *period* linear in DMX value, p at dmx_range
                                         lo/hi (Pot Strobe: measured)
              Safety cap: safe_max (normalised) and/or safe_max_hz; the lower one wins.
  fixed       constant DMX value that must never change (e.g. "keep in DMX mode")
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dmxreactive.dmx.universe import UNIVERSE_SIZE, Universe

KINDS = {"continuous", "select", "rate", "fixed"}


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Channel:
    name: str
    offset: int  # 0-based offset from the fixture start address
    kind: str
    fine_offset: int | None = None
    dmx_range: tuple[int, int] = (0, 255)
    slots: dict[str, int] = field(default_factory=dict)
    off: int = 0
    hz_range: tuple[float, float] | None = None
    period_ms: tuple[float, float] | None = None
    safe_max: float = 1.0
    safe_max_hz: float | None = None
    value: int = 0  # for kind == "fixed"
    default: Any = None  # normalised float, slot name or None


@dataclass(frozen=True)
class FixtureProfile:
    id: str
    manufacturer: str
    model: str
    mode: str
    channel_count: int
    channels: dict[str, Channel]
    blackout: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FixtureProfile:
        try:
            count = int(raw["channel_count"])
            channels: dict[str, Channel] = {}
            used_offsets: set[int] = set()
            for c in raw["channels"]:
                kind = c["kind"]
                if kind not in KINDS:
                    raise ProfileError(f"unknown channel kind {kind!r}")
                ch = Channel(
                    name=c["name"],
                    offset=int(c["offset"]),
                    kind=kind,
                    fine_offset=c.get("fine_offset"),
                    dmx_range=tuple(c.get("dmx_range", (0, 255))),
                    slots={k: int(v) for k, v in c.get("slots", {}).items()},
                    off=int(c.get("off", 0)),
                    hz_range=tuple(c["hz_range"]) if c.get("hz_range") else None,
                    period_ms=tuple(c["period_ms"]) if c.get("period_ms") else None,
                    safe_max=float(c.get("safe_max", 1.0)),
                    safe_max_hz=float(c["safe_max_hz"]) if c.get("safe_max_hz") else None,
                    value=int(c.get("value", 0)),
                    default=c.get("default"),
                )
                if ch.name in channels:
                    raise ProfileError(f"duplicate channel name {ch.name!r}")
                for off in (ch.offset, ch.fine_offset):
                    if off is None:
                        continue
                    if not 0 <= off < count:
                        raise ProfileError(f"{ch.name}: offset {off} outside channel_count {count}")
                    if off in used_offsets:
                        raise ProfileError(f"{ch.name}: offset {off} used twice")
                    used_offsets.add(off)
                if ch.hz_range and ch.period_ms:
                    raise ProfileError(f"{ch.name}: use hz_range or period_ms, not both")
                if ch.safe_max_hz and not (ch.hz_range or ch.period_ms):
                    raise ProfileError(f"{ch.name}: safe_max_hz needs hz_range or period_ms")
                if kind == "select" and not ch.slots:
                    raise ProfileError(f"{ch.name}: select channel needs slots")
                channels[ch.name] = ch
            profile = cls(
                id=raw["id"],
                manufacturer=raw.get("manufacturer", ""),
                model=raw.get("model", ""),
                mode=raw.get("mode", ""),
                channel_count=count,
                channels=channels,
                blackout=dict(raw.get("blackout", {})),
            )
        except KeyError as exc:
            raise ProfileError(f"missing key {exc}") from exc
        for name in profile.blackout:
            if name not in channels:
                raise ProfileError(f"blackout references unknown channel {name!r}")
        return profile

    @classmethod
    def load(cls, path: str | Path) -> FixtureProfile:
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _lerp_dmx(norm: float, lo: int, hi: int) -> int:
    return round(lo + _clamp01(norm) * (hi - lo))


class Fixture:
    """A patched fixture: holds current DMX values and writes them into a Universe."""

    def __init__(self, profile: FixtureProfile, address: int, name: str = "", group: str = ""):
        if not 1 <= address <= UNIVERSE_SIZE - profile.channel_count + 1:
            raise ProfileError(
                f"{name or profile.id}: address {address} does not fit "
                f"{profile.channel_count} channels in a 512-channel universe"
            )
        self.profile = profile
        self.address = address
        self.name = name or profile.id
        self.group = group
        self._values = [0] * profile.channel_count
        self.reset()

    @property
    def end_address(self) -> int:
        return self.address + self.profile.channel_count - 1

    def _channel(self, name: str) -> Channel:
        try:
            return self.profile.channels[name]
        except KeyError:
            raise KeyError(f"{self.name}: no channel {name!r}") from None

    def has(self, name: str) -> bool:
        return name in self.profile.channels

    def reset(self) -> None:
        """Restore every channel to its profile default (fixed channels to their fixed value)."""
        for ch in self.profile.channels.values():
            if ch.kind == "fixed":
                self._values[ch.offset] = ch.value
            elif ch.default is not None:
                self.set(ch.name, ch.default)
            else:
                self.set(ch.name, 0.0 if ch.kind != "select" else next(iter(ch.slots)))

    def set(self, name: str, value: float | str) -> None:
        """Set a channel by intent: normalised float, or a slot name for select channels."""
        ch = self._channel(name)
        if ch.kind == "fixed":
            raise ValueError(f"{self.name}.{name} is a fixed channel")
        if ch.kind == "select":
            if isinstance(value, str):
                if value not in ch.slots:
                    raise KeyError(f"{self.name}.{name}: unknown slot {value!r}")
                self._values[ch.offset] = ch.slots[value]
            else:
                # numeric: allow raw DMX position through normalised 0..1 over the whole range
                self._values[ch.offset] = _lerp_dmx(value, 0, 255)
        elif ch.kind == "continuous":
            lo, hi = ch.dmx_range
            if ch.fine_offset is not None:
                v16 = round(_clamp01(value) * 65535)
                self._values[ch.offset] = v16 >> 8
                self._values[ch.fine_offset] = v16 & 0xFF
            else:
                self._values[ch.offset] = _lerp_dmx(value, lo, hi)
        elif ch.kind == "rate":
            lo, hi = ch.dmx_range
            self._write_rate(ch, _lerp_dmx(value, lo, hi), on=value > 0)

    def _hz_to_dmx(self, ch: Channel, hz: float, floor: bool = False) -> int:
        lo, hi = ch.dmx_range
        if ch.period_ms is not None:
            p_lo, p_hi = ch.period_ms
            frac = (p_lo - 1000.0 / hz) / (p_lo - p_hi)
        elif ch.hz_range is not None:
            h_lo, h_hi = ch.hz_range
            frac = (hz - h_lo) / (h_hi - h_lo)
        else:
            raise ValueError(f"{self.name}.{ch.name} has no Hz mapping")
        if floor:  # for safety caps: never round up past the requested rate
            return math.floor(lo + _clamp01(frac) * (hi - lo))
        return _lerp_dmx(frac, lo, hi)

    def _rate_cap(self, ch: Channel) -> int:
        """Highest DMX value the safety limits allow on a rate channel."""
        lo, hi = ch.dmx_range
        cap = _lerp_dmx(ch.safe_max, lo, hi)
        if ch.safe_max_hz is not None:
            cap = min(cap, self._hz_to_dmx(ch, ch.safe_max_hz, floor=True))
        return cap

    def _write_rate(self, ch: Channel, dmx: int, on: bool) -> int:
        lo, _ = ch.dmx_range
        value = max(lo, min(int(dmx), self._rate_cap(ch))) if on else ch.off
        self._values[ch.offset] = value
        return value

    def set_raw(self, name: str, dmx: int, cap: int | None = None) -> int:
        """Set a rate channel to a raw DMX value (for calibration), limited by the safety cap.

        `cap` overrides the profile's safety cap for this call (calibration only; explicit).
        Returns the value actually sent. 0 (or the channel's `off` value) means off.
        """
        ch = self._channel(name)
        if ch.kind != "rate":
            raise ValueError(f"{self.name}.{name} is not a rate channel")
        if cap is None:
            return self._write_rate(ch, dmx, on=dmx > ch.off)
        lo, hi = ch.dmx_range
        value = ch.off if dmx <= ch.off else max(lo, min(int(dmx), cap, hi))
        self._values[ch.offset] = value
        return value

    def set_raw_dmx(self, name: str, dmx: int) -> None:
        """Write a raw DMX value (0..255) to a continuous/select channel's coarse byte."""
        ch = self._channel(name)
        if ch.kind not in ("continuous", "select"):
            raise ValueError(f"{self.name}.{name}: raw DMX only for continuous/select channels")
        self._values[ch.offset] = max(0, min(255, dmx))
        if ch.fine_offset is not None:
            self._values[ch.fine_offset] = 0

    def set_hz(self, name: str, hz: float) -> None:
        """Set a rate channel in Hz (needs hz_range or period_ms). 0 Hz turns it off."""
        ch = self._channel(name)
        if ch.kind != "rate" or not (ch.hz_range or ch.period_ms):
            raise ValueError(f"{self.name}.{name} has no Hz mapping")
        self._write_rate(ch, self._hz_to_dmx(ch, hz) if hz > 0 else 0, on=hz > 0)

    def max_hz(self, name: str) -> float | None:
        """Highest Hz the safety cap allows on a rate channel (None without an Hz mapping)."""
        ch = self._channel(name)
        cap = self._rate_cap(ch)
        lo, hi = ch.dmx_range
        frac = (cap - lo) / (hi - lo)
        if ch.period_ms is not None:
            p_lo, p_hi = ch.period_ms
            return 1000.0 / (p_lo + frac * (p_hi - p_lo))
        if ch.hz_range is not None:
            return ch.hz_range[0] + frac * (ch.hz_range[1] - ch.hz_range[0])
        return None

    def blackout(self) -> None:
        for name, value in self.profile.blackout.items():
            self.set(name, value)

    def dmx_values(self) -> list[int]:
        return list(self._values)

    def write(self, universe: Universe) -> None:
        for i, v in enumerate(self._values):
            universe.set(self.address + i, v)

    def describe(self) -> str:
        """Human-readable `channel=value` list, for the simulator and debugging."""
        parts = []
        for ch in self.profile.channels.values():
            v = self._values[ch.offset]
            if ch.fine_offset is not None:
                parts.append(f"{ch.name}={(v << 8) | self._values[ch.fine_offset]}/65535")
            elif ch.kind == "select":
                slot = next((s for s, d in ch.slots.items() if d == v), None)
                parts.append(f"{ch.name}={slot or v}")
            else:
                parts.append(f"{ch.name}={v}")
        return " ".join(parts)


class Patch:
    """All fixtures on one universe, with overlap validation."""

    def __init__(self, fixtures: list[Fixture]) -> None:
        self.fixtures = fixtures
        self._validate()

    def _validate(self) -> None:
        ordered = sorted(self.fixtures, key=lambda f: f.address)
        for a, b in zip(ordered, ordered[1:], strict=False):
            if b.address <= a.end_address:
                raise ProfileError(
                    f"DMX overlap: {a.name} ({a.address}-{a.end_address}) "
                    f"and {b.name} ({b.address}-{b.end_address})"
                )
        names = [f.name for f in self.fixtures]
        if len(set(names)) != len(names):
            raise ProfileError("fixture names must be unique")

    def get(self, name: str) -> Fixture:
        for f in self.fixtures:
            if f.name == name:
                return f
        raise KeyError(name)

    def group(self, group: str) -> list[Fixture]:
        return [f for f in self.fixtures if f.group == group]

    def blackout(self) -> None:
        for f in self.fixtures:
            f.blackout()

    def write(self, universe: Universe) -> None:
        for f in self.fixtures:
            f.write(universe)
