"""Measure Art-Net send jitter against a local fake node: python tools/jitter_check.py [seconds]."""

import statistics
import sys

from dmxreactive.app import run_output
from dmxreactive.config import load_config
from dmxreactive.dmx.fake_node import FakeNode

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 60
cfg = load_config()
with FakeNode() as node:
    loop, sender = run_output(cfg, lambda t: None, seconds, host="127.0.0.1", port=node.port)
    t = node.arrival_times
    gaps = [(b - a) * 1000 for a, b in zip(t, t[1:], strict=False)]
    s = loop.stats.summary()
    print(f"ticks={s['ticks']} deadline error: mean={s['mean_ms']:.3f} p95={s['p95_ms']:.3f} "
          f"max={s['max_ms']:.3f} ms; late={loop.stats.late_ticks}")
    print(f"packet gaps: mean={statistics.mean(gaps):.2f} ms stdev={statistics.stdev(gaps):.2f} ms "
          f"max={max(gaps):.2f} ms; seq gaps={node.sequence_gaps} errors={sender.send_errors}")
