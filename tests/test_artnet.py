import socket

import pytest

from dmxreactive.dmx.artnet import ArtNetSender, build_artdmx, parse_artdmx
from dmxreactive.dmx.fake_node import FakeNode
from dmxreactive.dmx.universe import Universe


def test_packet_layout_exact_bytes():
    data = bytes(range(10))
    pkt = build_artdmx(data, sequence=7, port_address=0x1234)
    assert pkt[:8] == b"Art-Net\x00"
    assert pkt[8:10] == b"\x00\x50"  # OpDmx 0x5000, little-endian
    assert pkt[10:12] == b"\x00\x0e"  # protocol version 14, big-endian
    assert pkt[12] == 7  # sequence
    assert pkt[13] == 0  # physical
    assert pkt[14] == 0x34 and pkt[15] == 0x12  # SubUni, Net
    assert pkt[16:18] == b"\x00\x0a"  # length, big-endian
    assert pkt[18:] == data


def test_odd_length_is_padded_to_even():
    pkt = build_artdmx(b"\x01\x02\x03")
    assert pkt[16:18] == b"\x00\x04" and pkt[18:] == b"\x01\x02\x03\x00"


@pytest.mark.parametrize("data", [b"", b"\x01", bytes(513)])
def test_bad_length_rejected(data):
    with pytest.raises(ValueError):
        build_artdmx(data)


def test_parse_roundtrip_and_rejects_garbage():
    pkt = parse_artdmx(build_artdmx(bytes(512), sequence=9, port_address=0x0021))
    assert (pkt.sequence, pkt.port_address, len(pkt.data)) == (9, 0x21, 512)
    with pytest.raises(ValueError):
        parse_artdmx(b"not artnet at all, definitely not")
    with pytest.raises(ValueError):
        parse_artdmx(build_artdmx(bytes(8))[:-3])  # truncated


def test_universe_clamps_and_bounds():
    u = Universe()
    u.set(1, 999)
    u.set(512, -5)
    assert u.get(1) == 255 and u.get(512) == 0
    for bad in (0, 513):
        with pytest.raises(ValueError):
            u.set(bad, 1)


def test_sender_to_fake_node_roundtrip():
    u = Universe()
    u.set(1, 10)
    u.set(512, 200)
    with FakeNode() as node:
        sender = ArtNetSender("127.0.0.1", node.port, port_address=0x0012)
        for _ in range(3):
            assert sender.send(u)
        assert node.wait_for_packets(3)
        assert node.frame[0] == 10 and node.frame[511] == 200
        assert node.port_address == 0x12
        assert node.sequence_gaps == 0 and node.invalid == 0
        sender.close()


def test_sequence_wraps_without_zero():
    u = Universe()
    with FakeNode() as node:
        sender = ArtNetSender("127.0.0.1", node.port)
        for _ in range(300):
            sender.send(u)
        assert node.wait_for_packets(280)
        assert node.sequence_gaps == 0  # 255 -> 1, never 0
        sender.close()


def test_send_error_is_counted_not_raised():
    sender = ArtNetSender("127.0.0.1", 9)
    sender._sock.close()
    sender._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender._sock.close()  # sendto on a closed socket raises OSError
    assert sender.send(Universe()) is False
    assert sender.send_errors == 1
