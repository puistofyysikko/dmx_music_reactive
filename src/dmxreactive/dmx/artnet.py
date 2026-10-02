"""Minimal Art-Net (ArtDmx) packet builder/parser and unicast UDP sender."""

from __future__ import annotations

import logging
import socket
import struct
from dataclasses import dataclass

from dmxreactive.dmx.universe import UNIVERSE_SIZE, Universe

log = logging.getLogger(__name__)

ARTNET_PORT = 6454
_HEADER = b"Art-Net\x00"
_OP_DMX = 0x5000
_PROTOCOL_VERSION = 14


def build_artdmx(data: bytes, sequence: int = 0, port_address: int = 0) -> bytes:
    """Build an ArtDmx packet.

    port_address is the 15-bit Art-Net address: net (7 bits) | subnet (4) | universe (4).
    sequence 0 disables ordering; otherwise 1..255.
    """
    if not 0 <= port_address < 0x8000:
        raise ValueError(f"port_address out of range: {port_address}")
    if not 0 <= sequence <= 255:
        raise ValueError(f"sequence out of range: {sequence}")
    if not 2 <= len(data) <= UNIVERSE_SIZE:
        raise ValueError(f"DMX data length must be 2..{UNIVERSE_SIZE}, got {len(data)}")
    if len(data) % 2:  # the Art-Net spec requires an even length
        data = data + b"\x00"
    return (
        _HEADER
        + struct.pack("<H", _OP_DMX)
        + struct.pack(">H", _PROTOCOL_VERSION)
        + bytes([sequence, 0, port_address & 0xFF, port_address >> 8])
        + struct.pack(">H", len(data))
        + data
    )


@dataclass(frozen=True)
class ArtDmxPacket:
    sequence: int
    physical: int
    port_address: int
    data: bytes


def parse_artdmx(packet: bytes) -> ArtDmxPacket:
    """Parse an ArtDmx packet; raises ValueError if it is not a valid one."""
    if len(packet) < 18 or packet[:8] != _HEADER:
        raise ValueError("not an Art-Net packet")
    (opcode,) = struct.unpack("<H", packet[8:10])
    if opcode != _OP_DMX:
        raise ValueError(f"not an ArtDmx packet (opcode 0x{opcode:04x})")
    (version,) = struct.unpack(">H", packet[10:12])
    if version < _PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version {version}")
    sequence, physical, sub_uni, net = packet[12], packet[13], packet[14], packet[15]
    (length,) = struct.unpack(">H", packet[16:18])
    data = packet[18 : 18 + length]
    if len(data) != length:
        raise ValueError("truncated ArtDmx payload")
    return ArtDmxPacket(sequence, physical, (net << 8) | sub_uni, data)


class ArtNetSender:
    """Sends full-universe ArtDmx frames by unicast UDP. Never raises on network errors."""

    def __init__(self, host: str, port: int = ARTNET_PORT, port_address: int = 0) -> None:
        self._addr = (host, port)
        self._port_address = port_address
        self._sequence = 0
        self.frames_sent = 0
        self.send_errors = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

    def send(self, universe: Universe) -> bool:
        """Send one frame. Returns False (and counts an error) if the OS refused it."""
        self._sequence = self._sequence % 255 + 1  # 1..255, never 0
        packet = build_artdmx(universe.as_bytes(), self._sequence, self._port_address)
        try:
            self._sock.sendto(packet, self._addr)
        except OSError as exc:
            self.send_errors += 1
            if self.send_errors == 1 or self.send_errors % 100 == 0:
                log.warning("Art-Net send failed (%d so far): %s", self.send_errors, exc)
            return False
        self.frames_sent += 1
        return True

    def close(self) -> None:
        self._sock.close()
