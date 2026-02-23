"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import pytest

from src.UI.utils import cme_ipc_protocol as protocol


def test_encode_decode_round_trip():
    envelope = protocol.build_envelope(
        "open_movie",
        payload={"interactive_url": "https://example.com/view", "title": "Demo"},
        message_id="abc123",
    )
    encoded = protocol.encode_envelope(envelope)
    decoded = protocol.decode_envelope(encoded)

    assert decoded["id"] == "abc123"
    assert decoded["type"] == "open_movie"
    assert decoded["payload"]["interactive_url"] == "https://example.com/view"


def test_extract_frames_handles_multiple_messages():
    part1 = protocol.encode_envelope(protocol.build_envelope("ping", message_id="1"))
    part2 = protocol.encode_envelope(protocol.build_envelope("pong", message_id="2"))
    messages, remaining = protocol.extract_frames(part1 + part2)

    assert len(messages) == 2
    assert messages[0]["type"] == "ping"
    assert messages[1]["type"] == "pong"
    assert remaining == b""


def test_decode_envelope_raises_on_malformed_json():
    with pytest.raises(ValueError):
        protocol.decode_envelope(b"{not-json}\n")


def test_build_socket_name_is_stable_and_safe():
    first = protocol.build_socket_name(prefix="my_helper", seed="seed-a")
    second = protocol.build_socket_name(prefix="my_helper", seed="seed-a")
    third = protocol.build_socket_name(prefix="my_helper", seed="seed-b")

    assert first == second
    assert first != third
    assert len(first) <= 180
    assert all(ch.isalnum() or ch in "._-" for ch in first)
