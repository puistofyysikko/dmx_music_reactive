"""Terminal status display for the live show (rich). `render(state)` is a pure function of the
live state so it can be tested; `StatusThread` redraws it ~8x per second off the render loop."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dmxreactive.analysis.features import BAND_NAMES

if TYPE_CHECKING:
    from dmxreactive.live import LiveState

_SPARK = " ▁▂▃▄▅▆▇█"
_COLOURS = {"white": "white", "red": "red", "orange": "dark_orange", "yellow": "yellow",
            "green": "green", "cyan": "cyan", "blue": "blue", "purple": "magenta"}
SCENE_STYLE = {"beat": "bold green", "drive": "bold red", "ambient": "bold cyan"}


def bar(value: float, width: int = 12) -> str:
    n = round(max(0.0, min(1.0, value)) * width)
    return "█" * n + "░" * (width - n)


def spark(value: float) -> str:
    return _SPARK[round(max(0.0, min(1.0, value)) * (len(_SPARK) - 1))]


def lamp(show_t: float, last_t: float, label: str, style: str, hold: float = 0.15) -> Text:
    on = show_t - last_t < hold
    return Text(f"● {label}", style=style if on else "grey30")


def _fixture_row(fx) -> tuple[Text, Text]:
    vals = fx.dmx_values()
    ch = fx.profile.channels

    def norm(name: str) -> float:
        c = ch[name]
        if c.fine_offset is not None:
            return ((vals[c.offset] << 8) | vals[c.fine_offset]) / 65535
        lo, hi = sorted(c.dmx_range)
        return (vals[c.offset] - lo) / max(1, hi - lo) if c.kind == "continuous" else vals[c.offset]

    parts = Text()
    if "dimmer" in ch:
        parts.append(f"dim {bar(vals[ch['dimmer'].offset] / 255, 10)} ")
    if "pan" in ch and "tilt" in ch:
        parts.append(f"pan {norm('pan'):.2f} tilt {norm('tilt'):.2f} ")
    if "color" in ch:
        v = vals[ch["color"].offset]
        name = next((s for s, d in ch["color"].slots.items() if d == v), "…")
        parts.append("■ ", style=_COLOURS.get(name, "white"))
        parts.append(f"{name} ")
    if "strobe" in ch and vals[ch["strobe"].offset] > 0:
        parts.append(" STROBE", style="bold yellow")
    return Text(f"{fx.name}", style="bold"), parts


def render(state: LiveState):
    show, cfg = state.show, state.config
    f, age = state.features()
    delay = cfg.show.light_delay_ms / 1000.0
    live_audio = f is not None and age <= 1.5 + delay

    head = Text()
    head.append(" LIVE ", style="bold white on green" if live_audio else "bold white on red")
    head.append(f"  → {state.target}  {state.packets_per_s:4.1f} pkts/s")
    if state.sender and state.sender.send_errors:
        head.append(f"  send errors {state.sender.send_errors}", style="bold red")
    if state.loop and state.loop.stats.late_ticks:
        head.append(f"  late ticks {state.loop.stats.late_ticks}", style="yellow")

    scene = Text()
    mood = show.mood.upper()
    forced = "" if show.override == "auto" else " (forced)"
    scene.append(f"{mood}{forced}", style=SCENE_STYLE.get(show.mood, "bold"))
    if show.blackout:
        scene.append("   BLACKOUT", style="bold white on red")
    if not show.strobe_enabled:
        scene.append("   FLASHING OFF", style="bold black on yellow")

    audio = Table.grid(padding=(0, 2))
    if live_audio:
        trend = "↑" if f.energy_trend > 0.25 else "↓" if f.energy_trend < -0.25 else "→"
        audio.add_row(
            Text(f"{f.bpm:5.1f} BPM", style="bold"),
            Text("LOCKED" if f.beat_locked else "free",
                 style="green" if f.beat_locked else "grey50"),
            Text(f"conf {f.beat_confidence:3.0%}"),
            Text(f"level {bar(f.level, 10)} {trend}"),
        )
        bands = " ".join(f"{n} {spark(v)}" for n, v in zip(BAND_NAMES, f.bands, strict=False))
        audio.add_row(Text(bands), Text(f"bass energy {bar(f.bass_energy, 8)}"),
                      Text("silent" if f.silent else ""), Text(""))
        barline = Text("bar ")
        trusted = f.bar_beat >= 0 and f.bar_confidence >= cfg.show.bar_min_confidence
        for n in range(4):
            on = trusted and f.bar_beat == n
            barline.append(f" {n + 1} ", style="bold black on green" if on and n == 0 else
                           "bold green" if on else "grey42")
        barline.append(f"  ({f.bar_confidence:.0%})" if f.bar_beat >= 0 else "  (?)",
                       style="grey50")
        audio.add_row(barline, Text(""), Text(""), Text(""))
    else:
        audio.add_row(Text("no audio from the capture (is anything playing? right device?)",
                           style="bold red"))

    lamps = Table.grid(padding=(0, 3))
    t = show.t
    lamps.add_row(lamp(t, show.last_beat_t, "BEAT", "bold green"),
                  lamp(t, show.last_kick_t, "KICK", "bold red"),
                  lamp(t, show.last_flash_t, "FLASH", "bold yellow", 0.2),
                  Text(f"flashes {show.flashes}   drops {show.drops}"))

    fixtures = Table.grid(padding=(0, 2))
    for fx in cfg.patch.fixtures:
        name, desc = _fixture_row(fx)
        fixtures.add_row(name, desc)

    st = state.source.stats
    health = Text(
        f"audio blocks {st.blocks} (dropped {st.dropped}, restarts {st.restarts})   "
        f"analysis errors {state.analysis.errors}   "
        f"master {show.master:.0%}   lead {cfg.show.beat_lead_ms:g} ms   "
        f"light delay {cfg.show.light_delay_ms:g} ms",
        style="grey62")

    events = Table.grid(padding=(0, 1))
    for et, msg in list(show.events)[-5:]:
        events.add_row(Text(f"{et:7.1f}s", style="grey50"), Text(msg))

    keys = Text("b blackout   s flashing on/off   a auto   1 beat   2 ambient   3 drive   "
                "+/- master   Ctrl-C quit" if state.keys_active else "(hotkeys off)",
                style="grey50")
    body = Group(scene, Text(""), audio, Text(""), lamps, Text(""), fixtures, Text(""), health,
                 Text(""), events, Text(""), keys)
    return Panel(body, title=head, title_align="left", border_style="green" if live_audio
                 else "red")


class StatusThread(threading.Thread):
    def __init__(self, state: LiveState, console: Console | None = None) -> None:
        super().__init__(name="status-ui", daemon=True)
        self._state = state
        self._console = console or Console()
        self._halt = threading.Event()

    def run(self) -> None:
        with Live(render(self._state), console=self._console, refresh_per_second=8,
                  transient=False) as live:
            while not self._halt.wait(0.12):
                try:
                    live.update(render(self._state))
                except Exception:  # a display glitch must never touch the show
                    pass

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=2)


def render_to_text(state: LiveState, width: int = 100) -> str:
    """The panel as plain text (tests, logs)."""
    console = Console(width=width, record=True, force_terminal=False, file=open("/dev/null", "w"))
    console.print(render(state))
    return console.export_text()

