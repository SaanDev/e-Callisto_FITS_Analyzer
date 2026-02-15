"""
Helpers for launching CME playback in a separate helper process.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QCoreApplication


@dataclass(frozen=True)
class LaunchResult:
    launched: bool
    command: List[str] = field(default_factory=list)
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    error: str = ""


def _spawn_kwargs() -> dict:
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
    return kwargs


def _frozen_executable_path() -> str:
    path = ""
    try:
        path = QCoreApplication.applicationFilePath()
    except Exception:
        path = ""
    path = str(path or "").strip()
    return path or sys.executable


def build_cme_helper_command(
    movie_url: str,
    movie_title: str = "",
    direct_movie_url: str = "",
) -> List[str]:
    text_url = str(movie_url or "").strip()
    title = str(movie_title or "").strip()

    if getattr(sys, "frozen", False):
        command = [_frozen_executable_path(), "--mode=cme-helper", "--movie-url", text_url]
    else:
        main_py = Path(__file__).resolve().parents[1] / "main.py"
        command = [sys.executable, str(main_py), "--mode=cme-helper", "--movie-url", text_url]

    if title:
        command.extend(["--movie-title", title])
    if str(direct_movie_url or "").strip():
        command.extend(["--movie-direct-url", str(direct_movie_url).strip()])
    return command


def launch_cme_helper(
    movie_url: str,
    movie_title: str = "",
    direct_movie_url: str = "",
    startup_timeout: float = 0.8,
) -> LaunchResult:
    text_url = str(movie_url or "").strip()
    if not text_url:
        return LaunchResult(launched=False, error="Missing movie URL.")

    command = build_cme_helper_command(
        text_url,
        movie_title,
        direct_movie_url=direct_movie_url,
    )
    try:
        process = subprocess.Popen(command, **_spawn_kwargs())
    except Exception as exc:
        return LaunchResult(launched=False, command=command, error=f"Failed to start helper: {exc}")

    timeout_s = max(0.0, float(startup_timeout))
    if timeout_s <= 0:
        return LaunchResult(launched=True, command=command, pid=process.pid)

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            return LaunchResult(
                launched=False,
                command=command,
                pid=process.pid,
                exit_code=exit_code,
                error=f"Helper exited immediately with code {exit_code}.",
            )
        time.sleep(0.05)

    return LaunchResult(launched=True, command=command, pid=process.pid)
