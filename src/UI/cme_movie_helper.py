"""
Isolated movie helper window for CME playback.
"""

from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.UI.utils.cme_ipc_protocol import build_envelope, decode_envelope, encode_envelope
from src.UI.utils.url_opener import open_url_robust


LOGGER = logging.getLogger("callisto.cme_helper")
if not LOGGER.handlers:
    LOGGER.addHandler(logging.NullHandler())


def _get_theme():
    app = QApplication.instance()
    if not app:
        return None
    return app.property("theme_manager")


class CMEMovieHelperWindow(QMainWindow):
    def __init__(
        self,
        movie_url: str = "",
        movie_title: str = "",
        direct_movie_url: str = "",
        theme=None,
        persistent_service: bool = False,
    ):
        super().__init__()
        self.movie_url = str(movie_url or "").strip()
        self.direct_movie_url = str(direct_movie_url or "").strip()
        self.movie_title = str(movie_title or "").strip() or "CME Movie"
        self.theme = theme or _get_theme()
        self._persistent_service = bool(persistent_service)
        self._allow_process_exit = False

        self._last_status_line = ""
        self._status_lines: list[str] = []

        self.setWindowTitle(self.movie_title)
        self.resize(1100, 760)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.top_panel = QWidget(self)
        controls = QHBoxLayout(self.top_panel)
        self.back_btn = QPushButton("Back")
        self.forward_btn = QPushButton("Forward")
        self.reload_btn = QPushButton("Reload")
        self.open_browser_btn = QPushButton("Open in Browser")
        self.open_direct_btn = QPushButton("Open Raw Movie")
        self.copy_url_btn = QPushButton("Copy URL")
        controls.addWidget(self.back_btn)
        controls.addWidget(self.forward_btn)
        controls.addWidget(self.reload_btn)
        controls.addWidget(self.open_browser_btn)
        controls.addWidget(self.open_direct_btn)
        controls.addWidget(self.copy_url_btn)
        layout.addWidget(self.top_panel)

        self.web_view = QWebEngineView(self)
        layout.addWidget(self.web_view, stretch=1)

        self.status_panel = QWidget(self)
        status_layout = QVBoxLayout(self.status_panel)
        self.status_label = QLabel("Starting CME movie helper...")
        status_layout.addWidget(self.status_label)

        self.status_details = QTextEdit(self)
        self.status_details.setReadOnly(True)
        self.status_details.setMinimumHeight(110)
        status_layout.addWidget(self.status_details)
        layout.addWidget(self.status_panel)

        self.back_btn.clicked.connect(self.web_view.back)
        self.forward_btn.clicked.connect(self.web_view.forward)
        self.reload_btn.clicked.connect(self.web_view.reload)
        self.open_browser_btn.clicked.connect(self.open_in_browser)
        self.open_direct_btn.clicked.connect(self.open_direct_movie)
        self.copy_url_btn.clicked.connect(self.copy_current_url)

        self.web_view.loadStarted.connect(self._on_load_started)
        self.web_view.loadFinished.connect(self._on_load_finished)
        self.web_view.urlChanged.connect(self._on_url_changed)

        if self.theme and hasattr(self.theme, "themeChanged"):
            self.theme.themeChanged.connect(self._on_theme_changed)
        if self.theme and hasattr(self.theme, "viewModeChanged"):
            self.theme.viewModeChanged.connect(lambda _mode: self._apply_theme_to_panels())

        self.open_direct_btn.setEnabled(bool(self.direct_movie_url))
        self._update_nav_buttons()
        self._apply_theme_to_panels()

    def set_persistent_service(self, enabled: bool) -> None:
        self._persistent_service = bool(enabled)

    def allow_process_exit(self) -> None:
        self._allow_process_exit = True

    def _set_status(self, text: str) -> None:
        self.status_label.setText(str(text or "").strip() or "Status unavailable.")

    def _append_status(self, text: str) -> None:
        line = str(text or "").strip()
        if not line or line == self._last_status_line:
            return

        self._last_status_line = line
        self._status_lines.append(line)
        if len(self._status_lines) > 80:
            self._status_lines = self._status_lines[-80:]

        self.status_details.setPlainText("\n".join(self._status_lines))
        bar = self.status_details.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _current_url_text(self) -> str:
        current = self.web_view.url()
        text = current.toString().strip() if current is not None else ""
        return text or self.movie_url

    def _focus_window(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _load_interactive_url(self, url_text: str) -> bool:
        text = str(url_text or "").strip()
        if not text:
            self._set_status("Missing movie URL.")
            self._append_status("Cannot start playback because URL is empty.")
            return False

        url = QUrl(text)
        if not url.isValid() or not url.scheme():
            self._set_status("Invalid movie URL.")
            self._append_status(f"Invalid URL: {text}")
            return False

        self._set_status("Loading CME interactive viewer...")
        self._append_status(f"Interactive URL: {text}")
        if self.direct_movie_url:
            self._append_status(f"Raw movie URL: {self.direct_movie_url}")
        self.web_view.setUrl(url)
        return True

    def apply_remote_theme(self, mode: str = "", view_mode: str = "") -> None:
        theme = self.theme or _get_theme()
        if theme is not None:
            mode_text = str(mode or "").strip().lower()
            if mode_text and hasattr(theme, "set_mode"):
                try:
                    theme.set_mode(mode_text)
                except Exception:
                    pass

            view_mode_text = str(view_mode or "").strip().lower()
            if view_mode_text and hasattr(theme, "set_view_mode"):
                try:
                    theme.set_view_mode(view_mode_text)
                except Exception:
                    pass
        self._apply_theme_to_panels()

    def open_movie(
        self,
        movie_url: str,
        movie_title: str = "",
        direct_movie_url: str = "",
        show_window: bool = True,
    ) -> bool:
        self.movie_url = str(movie_url or "").strip()
        if movie_title:
            self.movie_title = str(movie_title).strip() or self.movie_title
        self.direct_movie_url = str(direct_movie_url or "").strip()
        self.setWindowTitle(self.movie_title or "CME Movie")
        self.open_direct_btn.setEnabled(bool(self.direct_movie_url))
        ok = self._load_interactive_url(self.movie_url)
        if ok and show_window:
            self._focus_window()
        return ok

    def start_playback(self) -> None:
        self.open_movie(
            self.movie_url,
            self.movie_title,
            direct_movie_url=self.direct_movie_url,
            show_window=True,
        )

    def open_in_browser(self) -> None:
        url = self._current_url_text()
        result = open_url_robust(url)
        if result.opened:
            self._set_status("Opened in browser.")
            self._append_status(f"Fallback method: {result.method}")
        else:
            self._set_status("Could not open in browser.")
            self._append_status(result.error or "No fallback opener succeeded.")

    def open_direct_movie(self) -> None:
        target = self.direct_movie_url or self.movie_url
        result = open_url_robust(target)
        if result.opened:
            self._set_status("Opened raw movie URL.")
            self._append_status(f"Method: {result.method}")
            return

        self._set_status("Could not open raw movie URL.")
        self._append_status(result.error or "No fallback opener succeeded.")

    def copy_current_url(self) -> None:
        url = self._current_url_text()
        if not url:
            self._set_status("No URL available to copy.")
            return
        QApplication.clipboard().setText(url)
        self._set_status("Current URL copied to clipboard.")

    def _update_nav_buttons(self) -> None:
        history = self.web_view.history()
        self.back_btn.setEnabled(history.canGoBack())
        self.forward_btn.setEnabled(history.canGoForward())

    def _apply_theme_to_panels(self) -> None:
        theme = self.theme or _get_theme()
        if theme and hasattr(theme, "view_mode") and theme.view_mode() == "modern":
            self.top_panel.setAutoFillBackground(False)
            self.status_panel.setAutoFillBackground(False)
            return

        app = QApplication.instance()
        if not app:
            return
        app_pal = app.palette()

        top_pal = self.top_panel.palette()
        top_pal.setColor(QPalette.Window, app_pal.color(QPalette.AlternateBase))
        self.top_panel.setPalette(top_pal)
        self.top_panel.setAutoFillBackground(True)

        status_pal = self.status_panel.palette()
        status_pal.setColor(QPalette.Window, app_pal.color(QPalette.Base))
        self.status_panel.setPalette(status_pal)
        self.status_panel.setAutoFillBackground(True)

    def _on_theme_changed(self, _dark: bool) -> None:
        self._apply_theme_to_panels()

    def _on_load_started(self) -> None:
        self._set_status("Loading running-difference viewer and GOES X-ray plot...")
        self._update_nav_buttons()

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._set_status("Interactive viewer loaded.")
        else:
            self._set_status("Viewer load failed.")
            self._append_status("Failed to load interactive CME view. Try Open in Browser.")
        self._update_nav_buttons()

    def _on_url_changed(self, url: QUrl) -> None:
        text = str(url.toString() or "").strip()
        if text:
            self._append_status(f"Navigated to: {text}")
        self._update_nav_buttons()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._persistent_service and not self._allow_process_exit:
            self.hide()
            self._append_status("Helper window hidden. Service remains active.")
            event.ignore()
            return
        super().closeEvent(event)


class CMEHelperIPCService(QObject):
    OWNER_GUARD_INTERVAL_MS = 5000
    OWNER_PING_TIMEOUT_S = 50.0

    def __init__(
        self,
        app: QApplication,
        window: CMEMovieHelperWindow,
        ipc_name: str,
    ):
        super().__init__(window)
        self.app = app
        self.window = window
        self.ipc_name = str(ipc_name or "").strip()
        self.server = QLocalServer(self)
        self.socket: QLocalSocket | None = None
        self._socket_buffer = b""
        self._last_ping_monotonic = time.monotonic()
        self._guard_timer = QTimer(self)
        self._guard_timer.setInterval(self.OWNER_GUARD_INTERVAL_MS)
        self._guard_timer.timeout.connect(self._on_owner_guard_tick)

    def start(self) -> tuple[bool, str]:
        if not self.ipc_name:
            return False, "Missing IPC socket name."

        try:
            QLocalServer.removeServer(self.ipc_name)
        except Exception:
            pass

        if not self.server.listen(self.ipc_name):
            error = self.server.errorString() or "unknown listen error"
            return False, f"Failed to listen on helper IPC socket '{self.ipc_name}': {error}"

        self.server.newConnection.connect(self._on_new_connection)
        self.window.set_persistent_service(True)
        self.window.hide()
        self._last_ping_monotonic = time.monotonic()
        self._guard_timer.start()
        LOGGER.info("event=ipc_service_started pid=%s socket=%s", os.getpid(), self.ipc_name)
        return True, ""

    def _on_new_connection(self) -> None:
        while self.server.hasPendingConnections():
            incoming = self.server.nextPendingConnection()
            if incoming is None:
                continue

            if self.socket is not None:
                try:
                    self.socket.abort()
                except Exception:
                    pass

            self.socket = incoming
            self._socket_buffer = b""
            self._last_ping_monotonic = time.monotonic()
            self.socket.readyRead.connect(self._on_ready_read)
            self.socket.disconnected.connect(self._on_disconnected)
            LOGGER.info("event=ipc_client_connected pid=%s socket=%s", os.getpid(), self.ipc_name)

    def _on_disconnected(self) -> None:
        LOGGER.info("event=ipc_client_disconnected pid=%s socket=%s", os.getpid(), self.ipc_name)
        self.socket = None

    def _on_ready_read(self) -> None:
        socket = self.socket
        if socket is None:
            return

        self._socket_buffer += bytes(socket.readAll())
        while b"\n" in self._socket_buffer:
            line, self._socket_buffer = self._socket_buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                message = decode_envelope(line)
            except Exception as exc:
                self._send_message(
                    "error",
                    payload={"error": f"Malformed message: {exc}", "reason": "bad_frame"},
                )
                continue
            self._handle_message(message)

    def _send_message(
        self,
        message_type: str,
        payload: dict | None = None,
        message_id: str | None = None,
    ) -> None:
        socket = self.socket
        if socket is None or socket.state() != QLocalSocket.ConnectedState:
            return

        envelope = build_envelope(message_type, payload=payload, message_id=message_id)
        socket.write(encode_envelope(envelope))
        socket.flush()

    def _reply_error(self, request_id: str, reason: str, detail: str = "") -> None:
        self._send_message(
            "error",
            payload={
                "reason": str(reason or "unknown"),
                "error": str(detail or "Unknown helper error."),
            },
            message_id=request_id,
        )

    def _handle_message(self, message: dict) -> None:
        request_id = str(message.get("id") or "")
        msg_type = str(message.get("type") or "")
        payload = dict(message.get("payload") or {})

        try:
            if msg_type == "hello":
                self._last_ping_monotonic = time.monotonic()
                self._send_message(
                    "ack",
                    payload={
                        "service": "cme-helper",
                        "pid": os.getpid(),
                        "socket_name": self.ipc_name,
                    },
                    message_id=request_id,
                )
                return

            if msg_type == "ping":
                self._last_ping_monotonic = time.monotonic()
                self._send_message(
                    "pong",
                    payload={"pid": os.getpid(), "socket_name": self.ipc_name},
                    message_id=request_id,
                )
                return

            if msg_type == "show":
                self._last_ping_monotonic = time.monotonic()
                self.window._focus_window()
                self._send_message("ack", payload={"shown": True}, message_id=request_id)
                return

            if msg_type == "apply_theme":
                self._last_ping_monotonic = time.monotonic()
                self.window.apply_remote_theme(
                    mode=str(payload.get("mode") or ""),
                    view_mode=str(payload.get("view_mode") or ""),
                )
                self._send_message("ack", payload={"applied": True}, message_id=request_id)
                return

            if msg_type == "open_movie":
                self._last_ping_monotonic = time.monotonic()
                interactive_url = str(
                    payload.get("interactive_url")
                    or payload.get("movie_url")
                    or payload.get("url")
                    or ""
                ).strip()
                raw_url = str(payload.get("raw_url") or payload.get("direct_movie_url") or "").strip()
                title = str(payload.get("title") or payload.get("movie_title") or "CME Viewer").strip()

                if not interactive_url:
                    self._reply_error(request_id, "missing_url", "Missing interactive movie URL.")
                    return

                opened = self.window.open_movie(
                    interactive_url,
                    movie_title=title,
                    direct_movie_url=raw_url,
                    show_window=True,
                )
                if not opened:
                    self._reply_error(request_id, "open_failed", "Helper could not load movie URL.")
                    return

                self._send_message(
                    "ack",
                    payload={
                        "opened": True,
                        "interactive_url": interactive_url,
                        "raw_url": raw_url,
                    },
                    message_id=request_id,
                )
                return

            if msg_type == "shutdown":
                self._send_message("ack", payload={"shutting_down": True}, message_id=request_id)
                QTimer.singleShot(0, self._shutdown)
                return

            self._reply_error(request_id, "unsupported_command", f"Unsupported command: {msg_type}")
        except Exception as exc:
            self._reply_error(request_id, "handler_error", str(exc))

    def _on_owner_guard_tick(self) -> None:
        idle_s = time.monotonic() - self._last_ping_monotonic
        if idle_s <= self.OWNER_PING_TIMEOUT_S:
            return

        LOGGER.warning(
            "event=ipc_owner_timeout pid=%s socket=%s idle_seconds=%.2f",
            os.getpid(),
            self.ipc_name,
            idle_s,
        )
        self._shutdown()

    def _shutdown(self) -> None:
        self._guard_timer.stop()
        if self.socket is not None:
            try:
                self.socket.abort()
            except Exception:
                pass
            self.socket = None
        try:
            self.server.close()
        except Exception:
            pass
        self.window.allow_process_exit()
        self.window.close()
        self.app.quit()


def launch_cme_movie_helper(
    app: QApplication,
    movie_url: str,
    movie_title: str = "",
    direct_movie_url: str = "",
    theme=None,
    ipc_name: str = "",
) -> int:
    persistent = bool(str(ipc_name or "").strip())
    text_url = str(movie_url or "").strip()

    if not persistent:
        url = QUrl(text_url)
        if not text_url or not url.isValid() or not url.scheme():
            return 2

    window = CMEMovieHelperWindow(
        text_url,
        movie_title,
        direct_movie_url=direct_movie_url,
        theme=theme,
        persistent_service=persistent,
    )

    if persistent:
        service = CMEHelperIPCService(app, window, ipc_name=str(ipc_name).strip())
        ok, error = service.start()
        if not ok:
            LOGGER.error("event=ipc_service_start_failed error=%s", error)
            return 3
        app.setProperty("_cme_helper_service", service)
        return app.exec()

    window.show()
    window.start_playback()
    return app.exec()
