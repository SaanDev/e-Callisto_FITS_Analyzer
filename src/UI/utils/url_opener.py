"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from typing import List

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


@dataclass(frozen=True)
class OpenResult:
    opened: bool
    method: str
    error: str = ""


def _platform_open_command(url: str) -> List[str]:
    text = str(url or "").strip()
    if not text:
        return []

    if sys.platform.startswith("darwin"):
        return ["open", text]

    if sys.platform.startswith("win"):
        return ["cmd", "/c", "start", "", text]

    if sys.platform.startswith("linux"):
        if shutil.which("xdg-open"):
            return ["xdg-open", text]
        if shutil.which("gio"):
            return ["gio", "open", text]

    return []


def _spawn_command(command: List[str]) -> None:
    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        detached = getattr(subprocess, "DETACHED_PROCESS", 0)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = detached | new_group
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen(command, **kwargs)


def open_url_robust(url: str) -> OpenResult:
    text = str(url or "").strip()
    if not text:
        return OpenResult(opened=False, method="none", error="Empty URL.")

    errors = []

    try:
        if QDesktopServices.openUrl(QUrl(text)):
            return OpenResult(opened=True, method="qt_desktop")
        errors.append("QDesktopServices returned false")
    except Exception as exc:
        errors.append(f"QDesktopServices failed: {exc}")

    try:
        if webbrowser.open(text, new=2, autoraise=True):
            return OpenResult(opened=True, method="python_webbrowser")
        errors.append("webbrowser.open returned false")
    except Exception as exc:
        errors.append(f"webbrowser.open failed: {exc}")

    command = _platform_open_command(text)
    if command:
        try:
            _spawn_command(command)
            return OpenResult(opened=True, method="os_command")
        except Exception as exc:
            errors.append(f"os command failed: {exc}")
    else:
        errors.append("No platform opener command is available")

    return OpenResult(opened=False, method="none", error="; ".join(errors))

