"""Single-key terminal input (Linux/macOS) on a background thread."""

from __future__ import annotations

import select
import sys
import threading
from collections.abc import Callable


class KeyReader:
    """Calls `on_key(ch)` for every key pressed. No-op when stdin is not a terminal."""

    def __init__(self, on_key: Callable[[str], None]) -> None:
        self._on_key = on_key
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._saved = None
        self.active = False

    def start(self) -> None:
        if not sys.stdin.isatty():
            return
        import termios
        import tty

        self._saved = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
        self.active = True
        self._thread = threading.Thread(target=self._run, name="keys", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if ready:
                ch = sys.stdin.read(1)
                if ch:
                    self._on_key(ch)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self._saved is not None:
            import termios

            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._saved)
            self._saved = None
        self.active = False
