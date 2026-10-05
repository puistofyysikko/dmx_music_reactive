"""Soak test: run the whole live pipeline for hours and watch for leaks, drift and drops.

    python tools/soak.py --hours 3 [--log support/soak.log] [--sink dmxsoak]

Hardware-free and silent: a looping playlist of the tracks in support/tracks/ plays into a
virtual (null) sound card; the real capture path (parec on that sink's monitor) feeds the
analysis, the show runs at the configured rate and its Art-Net output goes to a fake node on
loopback. The real lights and speakers stay free. A line of metrics is logged every minute
and pass/fail criteria are checked at the end (exit code 0 = pass).
"""

from __future__ import annotations

import argparse
import glob
import os
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

from dmxreactive.audio.pulse import PulseMonitorSource
from dmxreactive.config import load_config
from dmxreactive.dmx.fake_node import FakeNode
from dmxreactive.live import LiveState, run_live


def rss_mb() -> float:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return 0.0


def playlist_files() -> list[str]:
    files = sorted(glob.glob("support/tracks/*.mp3")) + sorted(glob.glob("support/tracks/*.wav"))
    if not files:
        raise SystemExit("no tracks in support/tracks/ to play")
    return files


class Player(threading.Thread):
    """Loops the playlist into the virtual sink: ffmpeg decodes, paplay plays in real time."""

    def __init__(self, sink: str, files: list[str], log) -> None:
        super().__init__(name="soak-player", daemon=True)
        self.sink, self.files, self.log = sink, files, log
        self._stop_ev = threading.Event()
        self._proc: subprocess.Popen | None = None
        self.tracks_played = 0

    def run(self) -> None:
        while not self._stop_ev.is_set():
            for f in self.files:
                if self._stop_ev.is_set():
                    return
                cmd = (f"ffmpeg -v error -i {shlex.quote(f)} -f s16le -ar 48000 -ac 2 - | "
                       f"paplay --device={self.sink} --raw --format=s16le --rate=48000 "
                       f"--channels=2")
                self._proc = subprocess.Popen(["bash", "-c", cmd], start_new_session=True,
                                              stderr=subprocess.DEVNULL)
                self.log(f"playing {Path(f).name}")
                self._proc.wait()
                self.tracks_played += 1

    def stop(self) -> None:
        self._stop_ev.set()
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--minutes", type=float, help="alternative to --hours (for dry runs)")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between log lines")
    ap.add_argument("--sink", default="dmxsoak")
    ap.add_argument("--log", default=time.strftime("support/soak_%Y%m%d_%H%M.log"))
    args = ap.parse_args()
    duration = args.minutes * 60 if args.minutes else args.hours * 3600

    logfile = open(args.log, "a", buffering=1)

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        print(line, flush=True)
        logfile.write(line + "\n")

    cfg = load_config()
    module = subprocess.run(
        ["pactl", "load-module", "module-null-sink", f"sink_name={args.sink}"],
        capture_output=True, text=True, check=True).stdout.strip()
    log(f"soak start: {duration / 3600:.2f} h, virtual sink {args.sink} (module {module}), "
        f"output {cfg.rate_hz:g} Hz, block {cfg.audio.block_size}")
    node = FakeNode(record_times=False).start()
    player = Player(args.sink, playlist_files(), log)
    source = PulseMonitorSource(f"{args.sink}.monitor", cfg.audio.sample_rate,
                                cfg.audio.block_size, cfg.audio.latency_ms)
    samples: list[dict] = []
    t_start = time.monotonic()
    cpu0, wall0 = time.process_time(), time.monotonic()
    last_pk = {"t": t_start, "n": 0}
    next_log = {"t": t_start + args.interval}

    def on_status(state: LiveState) -> None:
        now = time.monotonic()
        if now < next_log["t"]:
            return
        next_log["t"] = now + args.interval
        c1, w1 = time.process_time(), now
        s = state.loop.stats.summary() if state.loop else {}
        rate = (node.packets - last_pk["n"]) / (now - last_pk["t"])
        last_pk.update(t=now, n=node.packets)
        sm = {
            "min": (now - t_start) / 60, "rss": rss_mb(), "cpu": 100 * (c1 - cpu0) / (w1 - wall0),
            "pps": rate, "gaps": node.sequence_gaps, "bad": node.invalid,
            "late": state.loop.stats.late_ticks if state.loop else 0,
            "p95": s.get("p95_ms", 0), "max": s.get("max_ms", 0),
            "drop": state.source.stats.dropped, "rst": state.source.stats.restarts,
            "errs": state.analysis.errors, "send_err": state.sender.send_errors,
            "fl": state.show.flashes, "dr": state.show.drops, "mood": state.show.mood,
            "thr": threading.active_count(), "fds": len(os.listdir("/proc/self/fd")),
        }
        samples.append(sm)
        log(f"{sm['min']:6.1f} min  rss {sm['rss']:6.1f} MB  cpu {sm['cpu']:4.0f}%  "
            f"{sm['pps']:5.1f} pkt/s  gaps {sm['gaps']}  bad {sm['bad']}  late {sm['late']}  "
            f"p95 {sm['p95']:.2f} ms  max {sm['max']:.1f} ms  audio drop {sm['drop']} "
            f"restart {sm['rst']}  errs {sm['errs']}/{sm['send_err']}  "
            f"{sm['mood']:<7} flashes {sm['fl']} drops {sm['dr']}  thr {sm['thr']} fds {sm['fds']}")

    show = None
    try:
        player.start()
        time.sleep(2.0)  # let the first track start so capture sees audio immediately
        show = run_live(cfg, source, "127.0.0.1", node.port, duration_s=duration, keys=False,
                        status=on_status)
    finally:
        player.stop()
        node.stop()
        subprocess.run(["pactl", "unload-module", module], capture_output=True)
        log("cleaned up (virtual sink removed)")

    # ---- verdict ------------------------------------------------------------------------
    problems = []
    if not samples:
        problems.append("run too short for a verdict")
    else:
        warm = [s for s in samples if s["min"] >= 5] or samples
        first, last = warm[0], samples[-1]
        if last["rss"] - first["rss"] > 15:
            problems.append(f"memory grew {last['rss'] - first['rss']:.1f} MB after warm-up")
        if last["thr"] != first["thr"] or last["fds"] - first["fds"] > 2:
            problems.append(f"threads {first['thr']}->{last['thr']}, fds {first['fds']}->"
                            f"{last['fds']}")
        if last["gaps"] or last["bad"]:
            problems.append(f"{last['gaps']} sequence gaps, {last['bad']} bad packets")
        if last["send_err"] or last["errs"]:
            problems.append(f"send errors {last['send_err']}, analysis errors {last['errs']}")
        total_ticks = max(1, int(samples[-1]["min"] * 60 * cfg.rate_hz))
        if last["late"] > 0.001 * total_ticks:
            problems.append(f"{last['late']} late ticks (> 0.1 %)")
        if last["rst"]:
            problems.append(f"audio capture restarted {last['rst']}x")
        blocks = max(1, int(samples[-1]["min"] * 60 * cfg.audio.sample_rate / cfg.audio.block_size))
        if last["drop"] > 0.001 * blocks:
            problems.append(f"{last['drop']} dropped audio blocks (> 0.1 %)")
        rates = [s["pps"] for s in samples[1:]] or [samples[0]["pps"]]
        if min(rates) < 0.97 * cfg.rate_hz:
            problems.append(f"packet rate dipped to {min(rates):.1f}/s")
    log("RESULT: " + ("PASS" if not problems else "FAIL - " + "; ".join(problems)))
    if show is not None:
        log(f"flashes {show.flashes}, drops {show.drops}, tracks played {player.tracks_played}")
    logfile.close()
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
