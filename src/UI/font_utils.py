"""
Shared font helpers for Qt and Matplotlib-facing UI code.
"""

from __future__ import annotations

import re
import sys
from typing import Iterable

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

_SIZE_SUFFIX_RE = re.compile(r"^(?P<name>.+?)\s+\d+(?:\.\d+)?$")


def available_font_families() -> set[str]:
    try:
        if QApplication.instance() is None:
            return set()
        return {str(name) for name in QFontDatabase.families()}
    except Exception:
        return set()


def normalize_font_family(value: str | None, *, available_families: Iterable[str] | None = None) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "default":
        return ""
    if text.startswith("."):
        return ""

    families = {str(name) for name in (available_families or ()) if str(name).strip()}
    candidates = [text]
    match = _SIZE_SUFFIX_RE.match(text)
    if match:
        base = str(match.group("name") or "").strip()
        if base:
            candidates.append(base)

    if families:
        lowered = {name.casefold(): name for name in families}
        for candidate in candidates:
            if candidate in families:
                return candidate
            resolved = lowered.get(candidate.casefold())
            if resolved:
                return resolved
        return ""

    return candidates[-1]


def preferred_ui_font_family(*, available_families: Iterable[str] | None = None) -> str:
    families = {str(name) for name in (available_families or available_font_families()) if str(name).strip()}
    if sys.platform == "darwin":
        candidates = (
            "Helvetica Neue",
            "Helvetica",
            "Arial",
            "Arial Rounded MT Bold",
        )
    elif sys.platform.startswith("win"):
        candidates = (
            "Segoe UI",
            "Arial",
            "Tahoma",
        )
    else:
        candidates = (
            "DejaVu Sans",
            "Liberation Sans",
            "Nimbus Sans",
            "Arial",
        )

    for candidate in candidates:
        normalized = normalize_font_family(candidate, available_families=families)
        if normalized:
            return normalized
    return ""


def sanitize_application_font(app: QApplication | None) -> str:
    if app is None:
        return ""
    try:
        families = available_font_families()
        font = app.font()
        family = normalize_font_family(font.family(), available_families=families)
        if not family:
            family = preferred_ui_font_family(available_families=families)
        if family and family != font.family():
            font.setFamily(family)
            app.setFont(font)
        return family
    except Exception:
        return ""
