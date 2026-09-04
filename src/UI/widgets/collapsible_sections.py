"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Accordion behaviour for sidebar group boxes.

Qt's checkable ``QGroupBox`` supplies the click affordance; unchecking hides the
group's direct children so the box shrinks to a single title row.  An arrow in
the title shows the state (the native indicator is hidden by the sidebar QSS).

Shared by the main window and the Solar Image Analysis window so both sidebars
behave identically.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QWidget

_BASE_TITLE_PROPERTY = "baseTitle"
_BASE_MARGINS_PROPERTY = "baseLayoutMargins"
_UNCONSTRAINED_HEIGHT = 16777215  # QWIDGETSIZE_MAX
_EXPANDED_ARROW = "▾  "
_COLLAPSED_ARROW = "▸  "


def group_base_title(group: QGroupBox) -> str:
    """Return a group's title without the accordion arrow."""
    base = group.property(_BASE_TITLE_PROPERTY)
    if base:
        return str(base)
    title = str(group.title() or "")
    for arrow in (_EXPANDED_ARROW, _COLLAPSED_ARROW):
        if title.startswith(arrow):
            return title[len(arrow):]
    return title


def _set_title_arrow(group: QGroupBox, expanded: bool) -> None:
    arrow = _EXPANDED_ARROW if expanded else _COLLAPSED_ARROW
    group.setTitle(arrow + group_base_title(group))


def set_group_expanded(group: QGroupBox, expanded: bool) -> None:
    """Expand or collapse one group, updating its arrow and children.

    Hiding the children is not enough on its own: the group's layout margins and
    the style's padding keep reserving a body, so a "collapsed" card still eats
    vertical space. Zeroing the layout margins and capping the height collapses
    it to the title row the user expects.
    """
    expanded = bool(expanded)
    group.setChecked(expanded)
    _set_title_arrow(group, expanded)
    for child in group.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
        child.setVisible(expanded)

    layout = group.layout()
    if layout is not None:
        if expanded:
            margins = group.property(_BASE_MARGINS_PROPERTY)
            if margins is not None:
                layout.setContentsMargins(*margins)
        else:
            if group.property(_BASE_MARGINS_PROPERTY) is None:
                current = layout.contentsMargins()
                group.setProperty(
                    _BASE_MARGINS_PROPERTY,
                    (current.left(), current.top(), current.right(), current.bottom()),
                )
            layout.setContentsMargins(0, 0, 0, 0)

    if expanded:
        group.setMaximumHeight(_UNCONSTRAINED_HEIGHT)
    else:
        group.setMaximumHeight(_collapsed_height(group))


def _collapsed_height(group: QGroupBox) -> int:
    """Height of a collapsed card: the title row plus the frame's own trim."""
    return int(group.fontMetrics().height() * 2)


def collapsible_groups(container: QWidget) -> list[QGroupBox]:
    """Return the accordion sections of a sidebar container, in layout order."""
    return list(container.findChildren(QGroupBox, options=Qt.FindDirectChildrenOnly))


def make_groups_collapsible(
    container: QWidget,
    *,
    on_expand=None,
    settings=None,
    settings_key: str = "",
) -> list[QGroupBox]:
    """Turn every direct-child group box of ``container`` into an accordion card.

    ``on_expand`` is called after a group is expanded: re-checking a checkable
    QGroupBox re-enables *all* of its children wholesale, so any window that
    gates controls on application state must re-derive that gating here.

    When ``settings`` and ``settings_key`` are given, each group's expanded state
    is restored on setup and saved on every toggle.
    """
    stored = _load_states(settings, settings_key)
    groups = collapsible_groups(container)

    for group in groups:
        base = str(group.title() or "")
        if not base:
            continue
        group.setProperty(_BASE_TITLE_PROPERTY, base)
        group.setCheckable(True)
        expanded = bool(stored.get(base, True))
        set_group_expanded(group, expanded)
        group.toggled.connect(
            lambda checked, g=group: _on_toggled(
                g, checked, on_expand=on_expand, settings=settings, settings_key=settings_key,
                container=container,
            )
        )

    return groups


def _on_toggled(group, expanded, *, on_expand, settings, settings_key, container) -> None:
    set_group_expanded(group, expanded)
    if expanded and callable(on_expand):
        on_expand()
    _save_states(container, settings, settings_key)


def _load_states(settings, settings_key: str) -> dict:
    if settings is None or not settings_key:
        return {}
    try:
        raw = settings.value(settings_key, "")
        payload = json.loads(str(raw or "") or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_states(container, settings, settings_key: str) -> None:
    if settings is None or not settings_key:
        return
    payload = {
        group_base_title(group): bool(group.isChecked())
        for group in collapsible_groups(container)
        if group.isCheckable() and group_base_title(group)
    }
    try:
        settings.setValue(settings_key, json.dumps(payload))
    except (TypeError, ValueError):
        pass
