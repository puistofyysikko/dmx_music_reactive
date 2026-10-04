"""Command-line entry point: check-config, sim, test-fixtures."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from dmxreactive.app import run_output
from dmxreactive.config import load_config
from dmxreactive.control.walker import Walk
from dmxreactive.dmx.fake_node import FakeNode
from dmxreactive.dmx.profiles import ProfileError


def _print_patch(config) -> None:
    print(f"Node {config.node.host}:{config.node.port}  port-address {config.node.port_address}"
          f"  rate {config.rate_hz:g} Hz")
    for f in sorted(config.patch.fixtures, key=lambda f: f.address):
        print(f"  {f.address:>3}-{f.end_address:<3} {f.name:<10} {f.profile.model} "
              f"({f.profile.mode})  group={f.group or '-'}")


def cmd_check_config(args) -> int:
    _print_patch(load_config(args.config))
    return 0


def cmd_sim(args) -> int:
    config = load_config(args.config)
    with FakeNode(args.bind, args.port) as node:
        print(f"Fake Art-Net node listening on {node.host}:{node.port} (Ctrl-C to stop)")
        last = None
        try:
            while True:
                frame = node.frame
                if frame != last:
                    last = frame
                    for f in config.patch.fixtures:
                        values = frame[f.address - 1 : f.end_address]
                        print(f"{f.name:<10} dmx={list(values)}")
                time.sleep(0.1)
        except KeyboardInterrupt:
            print(f"\n{node.packets} packets, {node.sequence_gaps} sequence gaps, "
                  f"{node.invalid} invalid")
    return 0


def cmd_test_fixtures(args) -> int:
    config = load_config(args.config)
    host, _, port = (args.target or "").partition(":")
    w = Walk(config.patch, args.step_seconds)
    print("Channel walk starting (Ctrl-C = blackout and exit)")
    run_output(config, w.update, duration_s=w.total + 0.2, host=host or None,
               port=int(port) if port else None)
    print("Done, blackout sent")
    return 0


def cmd_strobe_sweep(args) -> int:
    """Hold a series of raw strobe values so flash rates can be measured on camera."""
    config = load_config(args.config)
    host, _, port = (args.target or "").partition(":")
    fixture = config.patch.get(args.fixture)
    values = [int(v) for v in args.values.split(",")]
    if args.dimmer is not None and fixture.has("dimmer"):
        fixture.set("dimmer", args.dimmer)
    hold, gap = args.hold, args.gap
    schedule = []  # (start, end, requested value or None for gap)
    t = args.lead_in
    for v in values:
        schedule.append((t, t + hold, v))
        t += hold + gap
    total = t
    state = {"i": -1}

    def on_tick(elapsed: float) -> None:
        active = next((i for i, (a, b, _) in enumerate(schedule) if a <= elapsed < b), None)
        if active is None:
            fixture.set("strobe", 0.0)
            if state["i"] != -2 and state["i"] != -1:
                print("  (strobe off)")
                state["i"] = -2
            return
        if active != state["i"]:
            state["i"] = active
            sent = fixture.set_raw("strobe", schedule[active][2], args.max_value)
            note = "" if sent == schedule[active][2] else f" (capped from {schedule[active][2]})"
            print(f"[{elapsed:5.1f}s] strobe DMX value {sent}{note} for {hold:g}s")

    if args.max_value is not None:
        print(f"WARNING: safety cap overridden, strobe values up to {args.max_value} allowed")
    print(f"Strobe sweep on {fixture.name}: values {values}, {hold:g}s each, "
          f"{args.lead_in:g}s lead-in. Start the camera now. Ctrl-C = blackout.")
    run_output(config, on_tick, duration_s=total + 0.2, host=host or None,
               port=int(port) if port else None)
    print("Done, blackout sent")
    return 0


def cmd_list_devices(args) -> int:
    from dmxreactive.audio.devices import default_sink, list_sources

    sink = default_sink()
    print(f"Default output: {sink}")
    print(f"Capture target (@DEFAULT_MONITOR@): {sink}.monitor" if sink else "No default output")
    print("Sources (use the name in config audio.device; '*' = monitor of the default output):")
    for src in list_sources():
        mark = "*" if sink and src.name == f"{sink}.monitor" else " "
        kind = "monitor" if src.is_monitor else "input  "
        print(f" {mark} {kind} {src.state:<9} {src.name}")
    return 0


def _bar(db: float, width: int = 40, floor: float = -60.0) -> str:
    n = round(max(0.0, min(1.0, (db - floor) / -floor)) * width)
    return "#" * n + "-" * (width - n)


def cmd_meter(args) -> int:
    from dmxreactive.audio.factory import create_source
    from dmxreactive.audio.level import peak_dbfs, rms_dbfs

    config = load_config(args.config)
    source = create_source(config.audio, args.file, args.device)
    tty = sys.stdout.isatty()
    source.start()
    print(f"Level meter ({'file ' + args.file if args.file else 'live capture'}); Ctrl-C to stop")
    t0 = last = time.monotonic()
    rms_min, rms_max, silent_blocks = 0.0, -200.0, 0
    try:
        while args.seconds is None or time.monotonic() - t0 < args.seconds:
            block = source.read(1.0)
            if block is None:
                print("\nno audio for 1 s (is something playing? is the device right?)")
                continue
            rms, peak = rms_dbfs(block), peak_dbfs(block)
            rms_min, rms_max = min(rms_min, rms), max(rms_max, rms)
            silent_blocks += peak < -80
            now = time.monotonic()
            if now - last >= (0.05 if tty else 0.5):
                last = now
                line = f"RMS {rms:6.1f} dBFS  peak {peak:6.1f}  |{_bar(rms)}|"
                print("\r" + line if tty else line, end="" if tty else "\n", flush=True)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        source.stop()
    elapsed = time.monotonic() - t0
    st = source.stats
    print(f"\n{st.blocks} blocks in {elapsed:.1f}s ({st.blocks / elapsed:.1f}/s, expected "
          f"{config.audio.sample_rate / config.audio.block_size:.1f}/s), dropped {st.dropped}, "
          f"restarts {st.restarts}, near-silent blocks {silent_blocks}")
    return 0


_BLOCKS = " ▁▂▃▄▅▆▇█"


def _spark(v: float) -> str:
    return _BLOCKS[round(max(0.0, min(1.0, v)) * (len(_BLOCKS) - 1))]


def cmd_analyze(args) -> int:
    """Live view of what the analyser hears: level, bands, tempo, beat and kick markers."""
    from dmxreactive.analysis.analyzer import Analyzer
    from dmxreactive.audio.factory import create_source

    config = load_config(args.config)
    source = create_source(config.audio, args.file, args.device)
    analyzer = Analyzer(config.audio.sample_rate, config.audio.block_size)
    tty = sys.stdout.isatty()
    print(f"Analyser ({'file ' + args.file if args.file else 'live capture'}); "
          f"bands: bass lowmid mid high air.  Ctrl-C to stop")
    source.start()
    t0 = last_print = time.monotonic()
    beat_t = kick_t = -1.0
    n = 0
    try:
        while args.seconds is None or time.monotonic() - t0 < args.seconds:
            block = source.read(1.0)
            if block is None:
                continue
            f = analyzer.process(block)
            n += 1
            beat_t = f.t if f.beat else beat_t
            kick_t = f.t if f.kick else kick_t
            now = time.monotonic()
            if now - last_print >= (0.066 if tty else 0.5):
                last_print = now
                bands = "".join(_spark(v) for v in f.bands)
                line = (f"level {_spark(f.level)}{_spark(f.level)} bands {bands}  "
                        f"BPM {f.bpm:5.1f} conf {f.beat_confidence:3.0%} "
                        f"{'LOCKED' if f.beat_locked else 'free  '}  "
                        f"{'BEAT' if f.t - beat_t < 0.1 else '    '} "
                        f"{'KICK' if f.t - kick_t < 0.1 else '    '} "
                        f"{'(silent)' if f.silent else '        '} trend {f.energy_trend:+.1f}")
                print("\r" + line if tty else line, end="" if tty else "\n", flush=True)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        source.stop()
    print(f"\n{n} blocks analysed, dropped {source.stats.dropped}, "
          f"restarts {source.stats.restarts}")
    return 0


def cmd_record(args) -> int:
    """Record what the capture hears (system output) to a 16-bit mono WAV for offline analysis."""
    import wave

    import numpy as np

    from dmxreactive.audio.factory import create_source

    config = load_config(args.config)
    source = create_source(config.audio, args.file, args.device)
    source.start()
    t0 = time.monotonic()
    print(f"Recording {args.seconds:g} s to {args.output} "
          "(play the music now; Ctrl-C = stop early)")
    try:
        with wave.open(args.output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(config.audio.sample_rate)
            while time.monotonic() - t0 < args.seconds:
                block = source.read(1.0)
                if block is not None:
                    wav.writeframes((np.clip(block, -1, 1) * 32767).astype("<i2").tobytes())
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        source.stop()
    print(f"Saved {args.output} ({source.stats.blocks} blocks, dropped {source.stats.dropped})")
    return 0


def cmd_run(args) -> int:
    """Run the live show: capture -> analysis -> effects -> lights."""
    from dmxreactive.audio.factory import create_source
    from dmxreactive.live import run_live, status_line

    config = load_config(args.config)
    if args.flash_path != "both":
        from dataclasses import replace

        over = ({"kick_flash_strength": 2.0, "flash_gate": 0.0} if args.flash_path == "grid"
                else {"flash_gate": 2.0})
        config = replace(config, show=replace(config.show, **over))
    host, _, port = (args.target or "").partition(":")
    source = create_source(config.audio, args.file, args.device, loop=args.loop)
    where = f"{host or config.node.host}:{port or config.node.port}"
    use_tui = sys.stdout.isatty() and not args.plain
    ui = {"thread": None, "n": 0}

    def status(state) -> None:
        if use_tui:
            if ui["thread"] is None:
                from dmxreactive.control.tui import StatusThread

                ui["thread"] = StatusThread(state)
                ui["thread"].start()
        else:
            ui["n"] += 1
            if ui["n"] % 8 == 1:  # ~every 2 s
                print(status_line(state), flush=True)

    if not use_tui:
        print(f"Live show -> {where}  ({'file ' + args.file if args.file else 'live capture'}). "
              "Strobe flashes are capped; blackout key: b; quit: Ctrl-C.")
    try:
        show = run_live(config, source, host or None, int(port) if port else None,
                        args.seconds, keys=not args.no_keys, scene=args.scene, status=status,
                        on_message=None if use_tui else lambda m: print(f"[{m}]", flush=True))
    finally:
        if ui["thread"] is not None:
            ui["thread"].stop()
    print(f"Done, blackout sent. Flash events fired: {show.flashes}")
    return 0


def cmd_pulse_test(args) -> int:
    """Pulse one fixture's dimmer (or its built-in strobe) for several widths, to find out how
    short a flash the hardware can really show. Film it or watch the wall."""
    config = load_config(args.config)
    host, _, port = (args.target or "").partition(":")
    fixture = config.patch.get(args.fixture)
    widths = [float(w) / 1000.0 for w in args.widths.split(",")]
    schedule = []  # (start, end, width)
    t = args.gap
    for w in widths:
        for _ in range(args.repeat):
            schedule.append((t, t + max(w, 1 / config.rate_hz), w))
            t += max(w, 1 / config.rate_hz) + args.gap
    total = t
    state = {"i": -1}
    fixture.set("dimmer", args.base)

    def on_tick(elapsed: float) -> None:
        active = next((i for i, (a, b, _) in enumerate(schedule) if a <= elapsed < b), None)
        if args.mode == "dimmer":
            fixture.set("dimmer", args.level if active is not None else args.base)
        else:
            fixture.set("dimmer", args.level)
            if active is not None:
                fixture.set_hz("strobe", args.hz)
            else:
                fixture.set("strobe", 0.0)
        if active is not None and active != state["i"]:
            state["i"] = active
            print(f"[{elapsed:5.1f}s] pulse {schedule[active][2] * 1000:.0f} ms")

    what = ("dimmer pulses" if args.mode == "dimmer"
            else f"built-in strobe bursts at {args.hz:g} Hz")
    print(f"Pulse test on {fixture.name}: {what}, widths (ms) "
          f"{[round(w * 1000) for w in widths]} x{args.repeat}; one frame = "
          f"{1000 / config.rate_hz:.0f} ms. Ctrl-C = blackout.")
    run_output(config, on_tick, duration_s=total + 0.2, host=host or None,
               port=int(port) if port else None)
    print("Done, blackout sent")
    return 0


def cmd_preflight(args) -> int:
    """Check network, node, audio and config before a show; optionally walk the fixtures."""
    from dmxreactive.control.preflight import format_checks, ok, run_preflight

    config = load_config(args.config)
    print("Pre-flight checks")
    checks = run_preflight(config, audio=not args.no_audio)
    print(format_checks(checks))
    good = ok(checks)
    print("\nAll clear." if good else "\nFix the items marked ✘ before the show.")
    if args.walk and good:
        print("\nChannel walk (watch the fixtures; Ctrl-C = blackout)...")
        return cmd_test_fixtures(args)
    return 0 if good else 1


def cmd_hold(args) -> int:
    """Hold fixed channel values on one fixture (bring-up/debugging), then blackout."""
    config = load_config(args.config)
    host, _, port = (args.target or "").partition(":")
    fixture = config.patch.get(args.fixture)
    for item in args.settings:
        name, _, raw = item.partition("=")
        if not raw:
            raise ProfileError(f"expected name=value, got {item!r}")
        try:
            value: float | str = float(raw)
        except ValueError:
            value = raw  # a slot name such as colour=red
        if args.raw and isinstance(value, float):
            fixture.set_raw_dmx(name, int(value))
        else:
            fixture.set(name, value)
    print(f"Holding {fixture.name} for {args.seconds:g}s: {fixture.describe()}")
    print(f"DMX {fixture.address}-{fixture.end_address}: {fixture.dmx_values()}")
    run_output(config, lambda t: None, duration_s=args.seconds, host=host or None,
               port=int(port) if port else None)
    print("Done, blackout sent")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dmxreactive")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config", help="config file (default config/config.yaml, else example)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-config", help="validate config and print the patch").set_defaults(
        func=cmd_check_config)

    p = sub.add_parser("sim", help="run a fake Art-Net node and print received frames")
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=6454)
    p.set_defaults(func=cmd_sim)

    p = sub.add_parser("test-fixtures", help="step each fixture through its functions")
    p.add_argument("--target", help="override node as host[:port], e.g. 127.0.0.1 for the sim")
    p.add_argument("--step-seconds", type=float, default=2.0)
    p.set_defaults(func=cmd_test_fixtures)

    p = sub.add_parser("strobe-sweep", help="hold raw strobe values for camera measurement")
    p.add_argument("fixture", help="fixture name from the config, e.g. strobe1")
    p.add_argument("--values", default="2,30,60,90,103",
                   help="comma-separated raw DMX strobe values (capped by the profile safe_max)")
    p.add_argument("--hold", type=float, default=6.0, help="seconds per value")
    p.add_argument("--gap", type=float, default=3.0, help="dark seconds between values")
    p.add_argument("--lead-in", type=float, default=5.0, help="seconds before the first value")
    p.add_argument("--dimmer", type=float, default=1.0, help="dimmer level 0..1 while sweeping")
    p.add_argument("--max-value", type=int,
                   help="calibration only: raise the DMX cap above the profile's safe limit")
    p.add_argument("--target")
    p.set_defaults(func=cmd_strobe_sweep)

    p = sub.add_parser("analyze", help="live view of level, bands, tempo, beats and kicks")
    p.add_argument("--file", help="play this audio file in real time instead of live capture")
    p.add_argument("--device", help="capture source name (default: config audio.device)")
    p.add_argument("--seconds", type=float, help="stop after this many seconds")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("record", help="record the system output to a WAV (for offline analysis)")
    p.add_argument("output", help="WAV path, e.g. support/tracks/live_falco.wav")
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--file", help=argparse.SUPPRESS)
    p.add_argument("--device", help="capture source name (default: config audio.device)")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("run", help="run the live music-reactive show")
    p.add_argument("--file", help="drive the show from this audio file instead of live capture")
    p.add_argument("--loop", action="store_true", help="loop the --file")
    p.add_argument("--device", help="capture source name (default: config audio.device)")
    p.add_argument("--target", help="override node as host[:port], e.g. 127.0.0.1:16454 for sim")
    p.add_argument("--seconds", type=float, help="stop after this many seconds")
    p.add_argument("--scene", choices=["auto", "beat", "ambient", "drive"], help="force a scene")
    p.add_argument("--no-keys", action="store_true", help="disable hotkeys")
    p.add_argument("--plain", action="store_true", help="plain text status instead of the panel")
    p.add_argument("--flash-path", choices=["both", "grid", "kick"], default="both",
                   help="latency-test aid: strobe only on predicted beats (grid) or only on "
                   "detected kicks (kick)")
    p.set_defaults(func=cmd_run)

    sub.add_parser("list-devices", help="list audio capture sources").set_defaults(
        func=cmd_list_devices)

    p = sub.add_parser("meter", help="show the input level of the system output (or a file)")
    p.add_argument("--file", help="play this audio file in real time instead of live capture")
    p.add_argument("--device", help="capture source name (default: config audio.device)")
    p.add_argument("--seconds", type=float, help="stop after this many seconds")
    p.set_defaults(func=cmd_meter)

    p = sub.add_parser("pulse-test", help="pulse a fixture with several widths (hardware response)")
    p.add_argument("fixture", help="fixture name, e.g. mover1")
    p.add_argument("--mode", choices=["dimmer", "strobe"], default="dimmer",
                   help="pulse the dimmer, or burst the fixture's built-in strobe")
    p.add_argument("--widths", default="33,67,133,200,333,500", help="pulse widths in ms")
    p.add_argument("--repeat", type=int, default=2)
    p.add_argument("--level", type=float, default=1.0, help="dimmer level during a pulse (0..1)")
    p.add_argument("--base", type=float, default=0.0, help="dimmer level between pulses (0..1)")
    p.add_argument("--gap", type=float, default=1.5, help="seconds between pulses")
    p.add_argument("--hz", type=float, default=6.0, help="strobe rate for --mode strobe (capped)")
    p.add_argument("--target")
    p.set_defaults(func=cmd_pulse_test)

    p = sub.add_parser("preflight", help="check network, node (ArtPoll), audio and config")
    p.add_argument("--no-audio", action="store_true", help="skip the audio capture check")
    p.add_argument("--walk", action="store_true", help="after the checks, walk all fixtures")
    p.add_argument("--target", help="override node as host[:port]")
    p.add_argument("--step-seconds", type=float, default=1.5)
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("hold", help="hold channel values on one fixture, e.g. hold mover1 pan=0.2")
    p.add_argument("fixture")
    p.add_argument("settings", nargs="+", help="name=value (0..1, or slot name; --raw for DMX)")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--raw", action="store_true", help="values are raw DMX 0..255 (not rate/fixed)")
    p.add_argument("--target")
    p.set_defaults(func=cmd_hold)

    args = parser.parse_args(argv)
    if not args.config:  # e.g. DMXREACTIVE_CONFIG=config/config_bt.yaml scripts/start_show.sh
        args.config = os.environ.get("DMXREACTIVE_CONFIG") or None
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except (ProfileError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
