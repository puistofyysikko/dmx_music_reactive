"""Software pipeline latency: click track (real-time file source) -> analysis -> show -> Art-Net
packets, measured on a fake node on loopback. Excludes the sound-card capture path, the node
and the fixtures (those show up in a camera measurement, see tools/latency_analyze.py).

    python tools/pipeline_latency.py [bpm] [seconds] [--lead-ms N]
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np

from dmxreactive.audio.array_source import ArraySource, click_track
from dmxreactive.config import load_config
from dmxreactive.dmx.fake_node import FakeNode
from dmxreactive.live import run_live


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bpm", nargs="?", type=float, default=150.0)
    ap.add_argument("seconds", nargs="?", type=float, default=40.0)
    ap.add_argument("--lead-ms", type=float, help="override show.beat_lead_ms")
    ap.add_argument("--block", type=int, help="override audio.block_size")
    ap.add_argument("--rate", type=float, help="override output.rate_hz")
    ap.add_argument("--path", choices=["grid", "kick"], default="grid",
                    help="grid: predicted-beat flashes (lead applies); kick: flashes fired by "
                    "detected kicks (no lead possible)")
    args = ap.parse_args()

    cfg = load_config()
    if args.lead_ms is not None:
        cfg = replace(cfg, show=replace(cfg.show, beat_lead_ms=args.lead_ms))
    if args.block:
        cfg = replace(cfg, audio=replace(cfg.audio, block_size=args.block))
    if args.rate:
        cfg = replace(cfg, rate_hz=args.rate)
    # isolate one flash path (both together would flash twice per click)
    if args.path == "grid":
        cfg = replace(cfg, show=replace(cfg.show, kick_flash_strength=2.0, flash_gate=0.0))
    else:
        cfg = replace(cfg, show=replace(cfg.show, flash_gate=2.0))
    strobe = cfg.patch.get("strobe1")
    dimmer_addr = strobe.address + strobe.profile.channels["dimmer"].offset
    sr = cfg.audio.sample_rate
    x = click_track(args.bpm, args.seconds, sr)
    src = ArraySource(x, sr, cfg.audio.block_size, realtime=True)
    with FakeNode(history_channels=dimmer_addr) as node:
        run_live(cfg, src, "127.0.0.1", node.port, duration_s=args.seconds - 1, keys=False,
                 status=None, scene="beat")
        hist = list(node.history)
    t0 = src._t0  # the source's clock origin: the click at sample n "plays" at t0 + n/sr
    onsets, prev = [], 0
    for t, frame in hist:
        v = frame[dimmer_addr - 1] if len(frame) >= dimmer_addr else 0
        if v > 60 >= prev:
            onsets.append(t - t0)
        prev = v
    clicks = np.arange(0, args.seconds, 60 / args.bpm)
    lags = []
    for f in onsets:
        if f < 15:
            continue
        c = clicks[np.argmin(np.abs(clicks - f))]
        lags.append((f - c) * 1000)
    lags = np.array(lags)
    print(f"[{args.path}] {args.bpm:g} BPM, block {cfg.audio.block_size}, output "
          f"{cfg.rate_hz:g} Hz, lead {cfg.show.beat_lead_ms:g} ms: {len(lags)} flashes after lock")
    if len(lags):
        print(f"  flash packet minus click time: median {np.median(lags):+.0f} ms "
              f"(p10 {np.percentile(lags, 10):+.0f}, p90 {np.percentile(lags, 90):+.0f})")


if __name__ == "__main__":
    main()
