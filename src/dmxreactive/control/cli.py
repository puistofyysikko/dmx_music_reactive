"""Command-line entry point: check-config, sim, test-fixtures."""

from __future__ import annotations

import argparse
import logging
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

    p = sub.add_parser("hold", help="hold channel values on one fixture, e.g. hold mover1 pan=0.2")
    p.add_argument("fixture")
    p.add_argument("settings", nargs="+", help="name=value (0..1, or slot name; --raw for DMX)")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--raw", action="store_true", help="values are raw DMX 0..255 (not rate/fixed)")
    p.add_argument("--target")
    p.set_defaults(func=cmd_hold)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except (ProfileError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
