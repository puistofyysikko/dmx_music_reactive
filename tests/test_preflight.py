import socket
from pathlib import Path

from dmxreactive.config import load_config
from dmxreactive.control import preflight as pf
from dmxreactive.dmx.artnet import (
    build_artpoll,
    build_artpollreply,
    discover,
    parse_artpollreply,
)
from dmxreactive.dmx.fake_node import FakeNode

EXAMPLE = Path(__file__).parent.parent / "config" / "config.example.yaml"


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_artpoll_and_reply_roundtrip():
    assert build_artpoll()[:8] == b"Art-Net\x00" and len(build_artpoll()) == 14
    info = parse_artpollreply(build_artpollreply("192.168.1.20", 0, 0, 1, "PoE_Node_I_x"))
    assert (info.ip, info.short_name) == ("192.168.1.20", "PoE_Node_I_x")
    assert (info.net, info.subnet) == (0, 0)
    assert info.port_addresses() == [1]
    info2 = parse_artpollreply(build_artpollreply("10.0.0.2", 3, 5, 9))
    assert info2.port_addresses() == [(3 << 8) | (5 << 4) | 9]


def test_discover_finds_a_fake_node():
    reply_port = free_port()
    with FakeNode(poll_reply_to=("127.0.0.1", reply_port), port_address=1) as node:
        info = discover("127.0.0.1", node.port, timeout=2.0, bind_port=reply_port)
        assert info is not None and info.port_addresses() == [1]
        assert node.polls >= 1


def test_discover_returns_none_when_nobody_answers():
    with FakeNode() as node:  # does not answer polls
        assert discover("127.0.0.1", node.port, timeout=0.6, bind_port=free_port()) is None


def test_node_check_detects_a_universe_mismatch():
    cfg = load_config(EXAMPLE)  # config sends to port-address 1
    good = pf.check_node(cfg, parse_artpollreply(build_artpollreply("192.168.1.20", 0, 0, 1)))
    bad = pf.check_node(cfg, parse_artpollreply(build_artpollreply("192.168.1.20", 0, 0, 0)))
    assert good.status == pf.OK and bad.status == pf.FAIL and "universe" in bad.detail
    assert pf.check_node(cfg, None).status == pf.FAIL
    assert pf.check_node(cfg, None, error="Address already in use").status == pf.WARN


def test_network_check():
    cfg = load_config(EXAMPLE)
    assert pf.check_network(cfg, [("wlp0", "192.168.0.20/24")]).status == pf.FAIL
    ok = pf.check_network(cfg, [("wlp0", "192.168.0.20/24"), ("eth1", "192.168.1.10/24")])
    assert ok.status == pf.OK and "eth1" in ok.detail


def test_format_and_ok():
    checks = [pf.Check(pf.OK, "a", "fine"), pf.Check(pf.WARN, "b", "meh", "do x"),
              pf.Check(pf.FAIL, "c", "bad", "do y")]
    text = pf.format_checks(checks)
    assert "✔" in text and "✘" in text and "→ do y" in text
    assert pf.ok(checks[:2]) and not pf.ok(checks)


def test_bluetooth_output_triggers_a_latency_warning(monkeypatch):
    import numpy as np

    class Stub:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def read(self, timeout=1.0):
            return np.full(1024, 0.1, dtype=np.float32)

    sink = "bluez_output.AA_BB.1"
    monkeypatch.setattr(pf, "default_sink", lambda: sink)
    monkeypatch.setattr(pf, "list_sources", lambda: [type("S", (), {"name": sink + ".monitor"})()])
    monkeypatch.setattr("dmxreactive.audio.pulse.PulseMonitorSource", Stub)
    monkeypatch.setattr(pf.shutil, "which", lambda name: "/usr/bin/" + name)
    cfg = load_config(EXAMPLE)
    checks = pf.check_audio(cfg, capture_s=0.1)
    assert any(c.name == "bluetooth" and c.status == pf.WARN for c in checks)
    assert any(c.name == "audio capture" and c.status == pf.OK for c in checks)
