"""A software stand-in for the Art-Net node: receives ArtDmx over UDP and keeps the last frame."""

from __future__ import annotations

import socket
import threading
import time

from dmxreactive.dmx.artnet import parse_artdmx


class FakeNode:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, history_channels: int = 0) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, port))
        self._sock.settimeout(0.1)
        self.host, self.port = self._sock.getsockname()
        self._lock = threading.Lock()
        self._frame = bytes(512)
        self._port_address = 0
        self.packets = 0
        self.invalid = 0
        self.sequence_gaps = 0
        self.arrival_times: list[float] = []
        self._history_channels = history_channels
        self.history: list[tuple[float, bytes]] = []  # (arrival time, first N channels)
        self._last_seq = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="fake-node", daemon=True)

    def start(self) -> FakeNode:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._sock.close()

    def __enter__(self) -> FakeNode:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(1024)
            except TimeoutError:
                continue
            except OSError:
                return
            now = time.perf_counter()
            try:
                pkt = parse_artdmx(data)
            except ValueError:
                with self._lock:
                    self.invalid += 1
                continue
            with self._lock:
                if pkt.sequence and self._last_seq and pkt.sequence != self._last_seq % 255 + 1:
                    self.sequence_gaps += 1
                self._last_seq = pkt.sequence
                self._frame = pkt.data.ljust(512, b"\x00")
                self._port_address = pkt.port_address
                self.packets += 1
                self.arrival_times.append(now)
                if self._history_channels:
                    self.history.append((now, self._frame[: self._history_channels]))

    @property
    def frame(self) -> bytes:
        with self._lock:
            return self._frame

    @property
    def port_address(self) -> int:
        with self._lock:
            return self._port_address

    def wait_for_packets(self, n: int, timeout: float = 2.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self.packets >= n:
                    return True
            time.sleep(0.005)
        return False
