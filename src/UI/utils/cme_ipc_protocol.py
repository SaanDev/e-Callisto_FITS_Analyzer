"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import socket
import uuid
from typing import Any

PROTOCOL_VERSION = 1

MESSAGE_TYPES = {
    "hello",
    "open_movie",
    "apply_theme",
    "ping",
    "pong",
    "show",
    "shutdown",
    "ack",
    "error",
}

_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_UNIX_SOCKET_NAME_LIMIT = 92


def _sanitize_name(text: str) -> str:
    value = _SAFE_CHARS_RE.sub("_", str(text or "").strip())
    value = value.strip("_.-")
    return value or "cme"


def _truncate_socket_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return "cme_helper"
    if os.name == "nt":
        return text[:180]
    if len(text) <= _UNIX_SOCKET_NAME_LIMIT:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    keep = max(8, _UNIX_SOCKET_NAME_LIMIT - 11)
    return f"{text[:keep]}_{digest}"


def build_socket_name(prefix: str = "callisto_cme_helper", seed: str = "") -> str:
    safe_prefix = _sanitize_name(prefix)
    user = _sanitize_name(getpass.getuser() or os.environ.get("USER") or "user")
    uid = ""
    if hasattr(os, "getuid"):
        try:
            uid = str(os.getuid())
        except Exception:
            uid = ""
    host = _sanitize_name(socket.gethostname() or "host")
    source = f"{safe_prefix}|{user}|{uid}|{host}|{seed}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    raw = f"{safe_prefix}_{user}_{digest}"
    return _truncate_socket_name(raw)


def new_message_id() -> str:
    return uuid.uuid4().hex


def build_envelope(
    message_type: str,
    payload: dict[str, Any] | None = None,
    message_id: str | None = None,
    version: int = PROTOCOL_VERSION,
) -> dict[str, Any]:
    msg_type = str(message_type or "").strip()
    if msg_type not in MESSAGE_TYPES:
        raise ValueError(f"Unsupported message type: {msg_type!r}")

    envelope = {
        "version": int(version),
        "id": str(message_id or new_message_id()),
        "type": msg_type,
        "payload": dict(payload or {}),
    }
    return envelope


def encode_envelope(envelope: dict[str, Any]) -> bytes:
    return (json.dumps(envelope, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def decode_envelope(frame: bytes | str) -> dict[str, Any]:
    raw_text = frame.decode("utf-8") if isinstance(frame, (bytes, bytearray)) else str(frame or "")
    text = raw_text.strip()
    if not text:
        raise ValueError("Empty IPC frame.")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed IPC JSON frame: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("IPC frame must decode to a JSON object.")

    required = ("version", "id", "type", "payload")
    for field in required:
        if field not in parsed:
            raise ValueError(f"Missing IPC field: {field}")

    msg_type = str(parsed.get("type") or "").strip()
    if msg_type not in MESSAGE_TYPES:
        raise ValueError(f"Unsupported IPC message type: {msg_type!r}")

    payload = parsed.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("IPC payload must be an object.")

    return {
        "version": int(parsed.get("version", PROTOCOL_VERSION)),
        "id": str(parsed.get("id") or ""),
        "type": msg_type,
        "payload": payload,
    }


def extract_frames(buffer: bytes) -> tuple[list[dict[str, Any]], bytes]:
    messages: list[dict[str, Any]] = []
    remaining = bytes(buffer or b"")

    while True:
        idx = remaining.find(b"\n")
        if idx < 0:
            break

        line = remaining[:idx]
        remaining = remaining[idx + 1 :]
        if not line.strip():
            continue
        messages.append(decode_envelope(line))

    return messages, remaining
