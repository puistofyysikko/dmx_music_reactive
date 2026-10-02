import statistics
import threading
import time
from pathlib import Path

from dmxreactive.app import run_output
from dmxreactive.config import load_config
from dmxreactive.control.cli import main
from dmxreactive.control.walker import Walk
from dmxreactive.dmx.fake_node import FakeNode
from dmxreactive.dmx.frameloop import FrameLoop

EXAMPLE = str(Path(__file__).parent.parent / "config" / "config.example.yaml")


def level(fixture, channel):
    return fixture.dmx_values()[fixture.profile.channels[channel].offset]


def test_frameloop_holds_rate_without_drift():
    times = []
    loop = FrameLoop(30, lambda t: times.append(t))
    loop.run(duration_s=1.0)
    assert 29 <= len(times) <= 31
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert abs(statistics.mean(gaps) - 1 / 30) < 0.001
    assert loop.stats.summary()["p95_ms"] < 5  # acceptance criterion: jitter < 5 ms


def test_frameloop_stop_event():
    loop = FrameLoop(30, lambda t: None)
    threading.Timer(0.2, loop.stop_event.set).start()
    start = time.perf_counter()
    loop.run()
    assert time.perf_counter() - start < 1


def test_run_output_streams_and_blacks_out_on_exit():
    cfg = load_config(EXAMPLE)
    mover, strobe = cfg.patch.get("mover1"), cfg.patch.get("strobe1")

    def on_tick(t):
        mover.set("dimmer", 1.0)
        strobe.set("dimmer", 1.0)

    with FakeNode() as node:
        loop, sender = run_output(cfg, on_tick, duration_s=0.5, host="127.0.0.1", port=node.port)
        assert node.wait_for_packets(loop.stats.ticks + 3)
        time.sleep(0.05)
        assert 14 <= loop.stats.ticks <= 16
        assert node.sequence_gaps == 0 and sender.send_errors == 0
        frame = node.frame
        assert frame[7] == 0 and frame[9] == 0  # last frames are blackout


def test_walk_hits_every_function_and_ends_dark():
    cfg = load_config(EXAMPLE)
    seen = []
    w = Walk(cfg.patch, step_s=0.2, announce=seen.append)
    peak = {"dimmer": 0, "strobe": 0}
    t = 0.0
    while t < w.total + 0.1:
        w.update(t)
        for f in cfg.patch.fixtures:
            for name in peak:
                peak[name] = max(peak[name], level(f, name))
        t += 1 / 30
    labels = " | ".join(seen)
    for word in ("dimmer", "pan", "tilt", "colour red", "colour purple", "strobe 3 Hz"):
        assert word in labels
    assert "Hz unknown" not in labels  # both fixtures now have a measured Hz mapping
    assert peak["dimmer"] > 100 and peak["strobe"] > 0
    assert all(level(f, n) == 0 for f in cfg.patch.fixtures for n in peak)


def test_cli_check_config_and_bad_config(capsys):
    assert main(["--config", EXAMPLE, "check-config"]) == 0
    assert "PicoSpot 20 LED" in capsys.readouterr().out
    assert main(["--config", "/nonexistent.yaml", "check-config"]) == 2
