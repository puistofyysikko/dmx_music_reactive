"""Output runtime: frame loop -> patch -> universe -> Art-Net, with safe shutdown."""

from __future__ import annotations

import logging
import signal
from collections.abc import Callable

from dmxreactive.config import Config
from dmxreactive.dmx.artnet import ArtNetSender
from dmxreactive.dmx.frameloop import FrameLoop
from dmxreactive.dmx.universe import Universe

log = logging.getLogger(__name__)

SAFE_FRAMES = 3  # blackout frames sent on shutdown, in case one is lost


def run_output(
    config: Config,
    on_tick: Callable[[float], None],
    duration_s: float | None = None,
    host: str | None = None,
    port: int | None = None,
) -> tuple[FrameLoop, ArtNetSender]:
    """Run `on_tick(t)` at the configured rate and send the patch each frame.

    on_tick mutates fixtures in config.patch. On exit (normal, Ctrl-C, SIGTERM, exception)
    the patch is blacked out and sent several times.
    """
    universe = Universe()
    sender = ArtNetSender(
        host or config.node.host, port or config.node.port, config.node.port_address
    )

    def tick(t: float) -> None:
        on_tick(t)
        config.patch.write(universe)
        sender.send(universe)

    loop = FrameLoop(config.rate_hz, tick)
    previous = {s: signal.signal(s, lambda *_: loop.stop_event.set())
                for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        loop.run(duration_s)
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
        config.patch.blackout()
        config.patch.write(universe)
        for _ in range(SAFE_FRAMES):
            sender.send(universe)
        sender.close()
    return loop, sender
