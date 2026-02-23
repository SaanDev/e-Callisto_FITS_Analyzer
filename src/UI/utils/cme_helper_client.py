"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QTimer
from PySide6.QtNetwork import QLocalSocket

from src.UI.utils.cme_ipc_protocol import (
    build_envelope,
    build_socket_name,
    decode_envelope,
    encode_envelope,
    new_message_id,
)


LOGGER = logging.getLogger("callisto.cme_helper_client")
if not LOGGER.handlers:
    LOGGER.addHandler(logging.NullHandler())


def _spawn_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        detached = getattr(subprocess, "DETACHED_PROCESS", 0)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = detached | new_group
    else:
        kwargs["start_new_session"] = True
    return kwargs


@dataclass(frozen=True)
class HelperOpenResult:
    ok: bool
    method: str
    error: str = ""
    restart_attempted: bool = False


class CMEHelperClient(QObject):
    STARTUP_TIMEOUT_S = 6.0
    CONNECT_POLL_S = 0.15
    CONNECT_TIMEOUT_MS = 700
    COMMAND_TIMEOUT_MS = 4500
    HEARTBEAT_INTERVAL_MS = 8000
    HEARTBEAT_TIMEOUT_MS = 1200
    HEARTBEAT_FAIL_LIMIT = 2

    def __init__(
        self,
        theme_manager=None,
        ipc_name: str = "",
        logger: logging.Logger | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._logger = logger or LOGGER
        self._theme_manager = theme_manager
        app_path = str(QCoreApplication.applicationFilePath() or sys.executable)
        self._ipc_name = str(ipc_name or "").strip() or build_socket_name(seed=app_path)

        self._socket = QLocalSocket(self)
        self._socket.disconnected.connect(self._on_socket_disconnected)
        self._socket.errorOccurred.connect(self._on_socket_error)
        self._socket_buffer = b""
        self._pending_messages: list[dict[str, Any]] = []
        self._request_in_flight = False

        self._process: subprocess.Popen | None = None
        self._heartbeat_miss_count = 0
        self._last_open_payload: dict[str, Any] | None = None

        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(self.HEARTBEAT_INTERVAL_MS)
        self._heartbeat.timeout.connect(self._heartbeat_tick)

        self._bind_theme_signals()

    @property
    def ipc_name(self) -> str:
        return self._ipc_name

    def helper_pid(self) -> int | None:
        if self._process is None:
            return None
        return self._process.pid

    def ensure_started(self) -> tuple[bool, str]:
        if self._is_connected():
            return True, ""

        if self._is_process_running():
            ok, error = self._connect_and_handshake()
            if ok:
                return True, ""
            self._logger.warning(
                "event=helper_reconnect_failed reason=connect_existing error=%s pid=%s socket=%s",
                error,
                self.helper_pid(),
                self._ipc_name,
            )
            self._terminate_process()

        ok, error = self._start_process_and_handshake()
        if not ok:
            return False, error
        return True, ""

    def open_movie(self, interactive_url: str, raw_url: str = "", title: str = "") -> HelperOpenResult:
        payload = {
            "interactive_url": str(interactive_url or "").strip(),
            "raw_url": str(raw_url or "").strip(),
            "title": str(title or "").strip() or "CME Viewer",
        }
        if not payload["interactive_url"]:
            return HelperOpenResult(ok=False, method="ipc", error="Missing interactive movie URL.")

        self._last_open_payload = dict(payload)
        ok, error = self.ensure_started()
        if not ok:
            restart_ok, restart_error = self._restart_and_replay("startup_failed", payload)
            if restart_ok:
                return HelperOpenResult(
                    ok=True,
                    method="ipc_restart_replay",
                    restart_attempted=True,
                )
            return HelperOpenResult(
                ok=False,
                method="ipc",
                error=restart_error or error or "Failed to start helper process.",
                restart_attempted=True,
            )

        request_ok, _resp, request_error = self._send_request(
            "open_movie",
            payload=payload,
            expect_types={"ack"},
            timeout_ms=self.COMMAND_TIMEOUT_MS,
        )
        if request_ok:
            return HelperOpenResult(ok=True, method="ipc")

        restart_ok, restart_error = self._restart_and_replay("open_failed", payload)
        if restart_ok:
            return HelperOpenResult(
                ok=True,
                method="ipc_restart_replay",
                restart_attempted=True,
            )
        return HelperOpenResult(
            ok=False,
            method="ipc",
            error=restart_error or request_error or "Failed to open movie in helper.",
            restart_attempted=True,
        )

    def apply_theme(self, mode: str, view_mode: str) -> tuple[bool, str]:
        if not self._is_connected():
            return False, "Helper is not connected."

        ok, _resp, error = self._send_request(
            "apply_theme",
            payload={
                "mode": str(mode or "").strip(),
                "view_mode": str(view_mode or "").strip(),
            },
            expect_types={"ack"},
            timeout_ms=1800,
        )
        return ok, error

    def show(self) -> tuple[bool, str]:
        if not self._is_connected():
            ok, error = self.ensure_started()
            if not ok:
                return False, error

        ok, _resp, error = self._send_request(
            "show",
            payload={},
            expect_types={"ack"},
            timeout_ms=1800,
        )
        return ok, error

    def shutdown(self) -> None:
        self._heartbeat.stop()
        if self._is_connected():
            self._send_request(
                "shutdown",
                payload={"reason": "main_window_close"},
                expect_types={"ack"},
                timeout_ms=1200,
            )
        self._socket.abort()
        self._terminate_process()

    def _bind_theme_signals(self) -> None:
        theme = self._theme_manager
        if theme is None:
            return
        if hasattr(theme, "themeChanged"):
            try:
                theme.themeChanged.connect(lambda _dark: self._push_theme_if_connected())
            except Exception:
                pass
        if hasattr(theme, "viewModeChanged"):
            try:
                theme.viewModeChanged.connect(lambda _mode: self._push_theme_if_connected())
            except Exception:
                pass

    def _theme_mode(self) -> str:
        theme = self._theme_manager
        if theme is not None and hasattr(theme, "mode"):
            try:
                return str(theme.mode() or "system").strip().lower()
            except Exception:
                pass
        return "system"

    def _theme_view_mode(self) -> str:
        theme = self._theme_manager
        if theme is not None and hasattr(theme, "view_mode"):
            try:
                return str(theme.view_mode() or "modern").strip().lower()
            except Exception:
                pass
        return "modern"

    def _push_theme_if_connected(self) -> None:
        if not self._is_connected():
            return
        mode = self._theme_mode()
        view_mode = self._theme_view_mode()
        ok, error = self.apply_theme(mode, view_mode)
        if ok:
            self._logger.info(
                "event=helper_apply_theme mode=%s view_mode=%s socket=%s",
                mode,
                view_mode,
                self._ipc_name,
            )
        elif error:
            self._logger.warning(
                "event=helper_apply_theme_failed error=%s socket=%s",
                error,
                self._ipc_name,
            )

    def _helper_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            binary = str(QCoreApplication.applicationFilePath() or sys.executable)
            return [binary, "--mode=cme-helper", "--ipc-name", self._ipc_name]

        main_py = Path(__file__).resolve().parents[1] / "main.py"
        return [sys.executable, str(main_py), "--mode=cme-helper", "--ipc-name", self._ipc_name]

    def _start_process_and_handshake(self) -> tuple[bool, str]:
        command = self._helper_command()
        try:
            self._process = subprocess.Popen(command, **_spawn_kwargs())
        except Exception as exc:
            return False, f"Failed to spawn helper: {exc}"

        self._logger.info(
            "event=helper_spawned pid=%s socket=%s command=%s",
            self.helper_pid(),
            self._ipc_name,
            " ".join(shlex.quote(part) for part in command),
        )

        deadline = time.monotonic() + self.STARTUP_TIMEOUT_S
        last_error = ""
        while time.monotonic() < deadline:
            if not self._is_process_running():
                exit_code = self._process.poll() if self._process is not None else None
                return False, f"Helper exited before handshake (exit code {exit_code})."

            ok, error = self._connect_and_handshake()
            if ok:
                return True, ""
            last_error = error
            time.sleep(self.CONNECT_POLL_S)

        return False, last_error or "Timed out waiting for helper IPC handshake."

    def _connect_and_handshake(self) -> tuple[bool, str]:
        self._socket.abort()
        self._socket_buffer = b""
        self._pending_messages = []

        self._socket.connectToServer(self._ipc_name)
        if not self._socket.waitForConnected(self.CONNECT_TIMEOUT_MS):
            return False, self._socket.errorString() or "Could not connect to helper socket."

        ok, _resp, error = self._send_request(
            "hello",
            payload={"client_pid": os.getpid()},
            expect_types={"ack"},
            timeout_ms=self.COMMAND_TIMEOUT_MS,
        )
        if not ok:
            self._socket.abort()
            return False, f"Helper handshake failed: {error}"

        self._heartbeat_miss_count = 0
        self._heartbeat.start()
        self._push_theme_if_connected()
        self._logger.info(
            "event=helper_connected pid=%s socket=%s",
            self.helper_pid(),
            self._ipc_name,
        )
        return True, ""

    def _is_process_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _terminate_process(self) -> None:
        if self._process is None:
            return

        if self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=0.8)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._process = None
        self._heartbeat.stop()
        self._socket.abort()

    def _is_connected(self) -> bool:
        return self._socket.state() == QLocalSocket.ConnectedState

    def _send_request(
        self,
        message_type: str,
        payload: dict[str, Any] | None,
        expect_types: set[str],
        timeout_ms: int,
    ) -> tuple[bool, dict[str, Any], str]:
        if not self._is_connected():
            return False, {}, "IPC socket is not connected."
        if self._request_in_flight:
            return False, {}, "IPC request already in progress."

        self._request_in_flight = True
        try:
            request_id = new_message_id()
            envelope = build_envelope(message_type, payload=payload, message_id=request_id)
            self._socket.write(encode_envelope(envelope))
            if not self._socket.waitForBytesWritten(timeout_ms):
                return False, {}, "Failed writing to helper socket."
            return self._wait_for_response(request_id, expect_types, timeout_ms)
        except Exception as exc:
            return False, {}, str(exc)
        finally:
            self._request_in_flight = False

    def _wait_for_response(
        self,
        request_id: str,
        expect_types: set[str],
        timeout_ms: int,
    ) -> tuple[bool, dict[str, Any], str]:
        deadline = time.monotonic() + max(0.1, float(timeout_ms) / 1000.0)

        while time.monotonic() < deadline:
            idx = self._find_pending_index(request_id)
            if idx >= 0:
                message = self._pending_messages.pop(idx)
                msg_type = str(message.get("type") or "")
                payload = dict(message.get("payload") or {})
                if msg_type in expect_types:
                    return True, payload, ""
                if msg_type == "error":
                    error = str(payload.get("error") or payload.get("reason") or "Helper returned error.")
                    return False, payload, error
                return False, payload, f"Unexpected helper response type: {msg_type}"

            remaining_ms = int(max(1, (deadline - time.monotonic()) * 1000))
            self._pump_incoming(min(250, remaining_ms))

            if not self._is_connected():
                break

        return False, {}, "Timed out waiting for helper response."

    def _find_pending_index(self, request_id: str) -> int:
        for idx, message in enumerate(self._pending_messages):
            if str(message.get("id") or "") == request_id:
                return idx
        return -1

    def _pump_incoming(self, wait_ms: int) -> None:
        if not self._is_connected():
            return

        if self._socket.bytesAvailable() <= 0:
            self._socket.waitForReadyRead(max(1, int(wait_ms)))

        if self._socket.bytesAvailable() <= 0:
            return

        self._socket_buffer += bytes(self._socket.readAll())
        while b"\n" in self._socket_buffer:
            line, self._socket_buffer = self._socket_buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                message = decode_envelope(line)
            except Exception as exc:
                self._logger.warning(
                    "event=helper_bad_frame error=%s socket=%s",
                    exc,
                    self._ipc_name,
                )
                continue
            self._pending_messages.append(message)

    def _restart_and_replay(self, reason: str, payload: dict[str, Any] | None) -> tuple[bool, str]:
        self._logger.warning(
            "event=helper_restart_attempt reason=%s pid=%s socket=%s",
            reason,
            self.helper_pid(),
            self._ipc_name,
        )

        self._terminate_process()
        ok, error = self._start_process_and_handshake()
        if not ok:
            return False, error

        if not payload:
            return True, ""

        request_ok, _resp, request_error = self._send_request(
            "open_movie",
            payload=payload,
            expect_types={"ack"},
            timeout_ms=self.COMMAND_TIMEOUT_MS,
        )
        if request_ok:
            self._logger.info(
                "event=helper_restart_replay_success pid=%s socket=%s",
                self.helper_pid(),
                self._ipc_name,
            )
            return True, ""

        self._logger.error(
            "event=helper_restart_replay_failed error=%s pid=%s socket=%s",
            request_error,
            self.helper_pid(),
            self._ipc_name,
        )
        return False, request_error or "Helper restart replay failed."

    def _heartbeat_tick(self) -> None:
        if not self._is_connected():
            self._heartbeat.stop()
            return
        if self._request_in_flight:
            return

        ok, _resp, error = self._send_request(
            "ping",
            payload={},
            expect_types={"pong"},
            timeout_ms=self.HEARTBEAT_TIMEOUT_MS,
        )
        if ok:
            self._heartbeat_miss_count = 0
            return

        self._heartbeat_miss_count += 1
        self._logger.warning(
            "event=helper_heartbeat_miss misses=%s error=%s pid=%s socket=%s",
            self._heartbeat_miss_count,
            error,
            self.helper_pid(),
            self._ipc_name,
        )
        if self._heartbeat_miss_count < self.HEARTBEAT_FAIL_LIMIT:
            return

        payload = dict(self._last_open_payload or {})
        restart_ok, restart_error = self._restart_and_replay("heartbeat_timeout", payload)
        if restart_ok:
            self._heartbeat_miss_count = 0
            return

        self._logger.error(
            "event=helper_heartbeat_recovery_failed error=%s pid=%s socket=%s",
            restart_error,
            self.helper_pid(),
            self._ipc_name,
        )

    def _on_socket_disconnected(self) -> None:
        self._heartbeat.stop()

    def _on_socket_error(self, _error_code) -> None:
        if self._is_connected():
            return
        self._heartbeat.stop()
