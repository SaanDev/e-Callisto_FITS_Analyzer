"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Collapsible sidebar sections.

Each section is a card with a clickable header row and a content area, in the
shape of the well-known PySide6 collapsible container: click the header, the
body shows or hides.  There is no check box — the header itself is the control,
and its arrow shows the state.

An earlier version made the section's ``QGroupBox`` checkable instead.  That put
a check box where a header belonged, re-enabled every child whenever a card was
re-opened, and needed height caps to stop a "collapsed" card reserving a body.
Wrapping the group box in a real header/content pair removes all three problems:
collapsing just hides a widget and the layout does the rest.

Sections are built by :func:`make_groups_collapsible`, which wraps the group
boxes a window has already built, so windows keep their own attribute names
(``self.units_group_box`` and friends) and their own inner layouts.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QGroupBox, QSizePolicy, QToolButton, QVBoxLayout, QWidget

_BASE_TITLE_PROPERTY = "baseTitle"
_SECTION_BODY_PROPERTY = "sectionBody"
_COLLAPSED_PROPERTY = "collapsed"


def _header_text(title: str) -> str:
    """Label for the header button, spaced off its arrow.

    A button treats "&" as a mnemonic marker, so "Display & Crop" would render
    as "Display _Crop"; doubling it prints a literal ampersand.
    """
    return "  " + str(title or "").replace("&", "&&")


class CollapsibleSection(QWidget):
    """A card whose header row expands and collapses its content."""

    toggled = Signal(bool)

    #: Inset of the content inside the card (left, top, right, bottom).
    BODY_MARGINS = (14, 0, 14, 14)

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("CollapsibleSection")
        # A plain QWidget ignores a stylesheet background unless it is told to
        # let the style paint it, which is what makes the card visible at all.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.setProperty(_BASE_TITLE_PROPERTY, str(title or ""))

        self._header = QToolButton(self)
        self._header.setObjectName("SectionHeader")
        self._header.setText(_header_text(title))
        self._header.setCheckable(True)
        self._header.setChecked(True)
        self._header.setArrowType(Qt.DownArrow)
        self._header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._header.setAutoRaise(True)
        self._header.setIconSize(QSize(16, 16))
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setFocusPolicy(Qt.StrongFocus)
        self._header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._header.clicked.connect(self._on_header_clicked)

        self._body = QWidget(self)
        self._body.setObjectName("SectionBody")
        self._body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._body_layout = QVBoxLayout(self._body)
        # Real margins, not stylesheet padding: QSS padding does not inset a
        # plain widget's layout.
        self._body_layout.setContentsMargins(*self.BODY_MARGINS)
        self._body_layout.setSpacing(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header)
        layout.addWidget(self._body)

        self._content: QWidget | None = None
        self._sync_state(True)

    # ---- content
    def setContentWidget(self, widget: QWidget) -> None:
        """Adopt ``widget`` as this section's body."""
        if self._content is widget:
            return
        if self._content is not None:
            self._body_layout.removeWidget(self._content)
            self._content.setParent(None)
        self._content = widget
        if widget is not None:
            self._body_layout.addWidget(widget)
            widget.setVisible(self.isExpanded())

    def contentWidget(self) -> QWidget | None:
        return self._content

    def header(self) -> QToolButton:
        return self._header

    # ---- title
    def title(self) -> str:
        return str(self.property(_BASE_TITLE_PROPERTY) or "")

    def setTitle(self, title: str) -> None:
        text = str(title or "")
        self.setProperty(_BASE_TITLE_PROPERTY, text)
        self._header.setText(_header_text(text))

    # ---- state
    def isExpanded(self) -> bool:
        return bool(self._header.isChecked())

    def setExpanded(self, expanded: bool, *, notify: bool = True) -> None:
        expanded = bool(expanded)
        changed = expanded != self.isExpanded()
        self._header.setChecked(expanded)
        self._sync_state(expanded)
        if notify and changed:
            self.toggled.emit(expanded)

    def toggle(self) -> None:
        self.setExpanded(not self.isExpanded())

    def expand(self) -> None:
        self.setExpanded(True)

    def collapse(self) -> None:
        self.setExpanded(False)

    def _on_header_clicked(self, checked: bool) -> None:
        self._sync_state(bool(checked))
        self.toggled.emit(bool(checked))

    def _sync_state(self, expanded: bool) -> None:
        self._header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._body.setVisible(expanded)
        content = self._content
        if content is not None:
            # Hide the children as well: code that asks one specific control
            # whether it is hidden must still get the right answer inside a
            # collapsed card (Qt does not report a widget as hidden merely
            # because an ancestor is).
            for child in content.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
                child.setVisible(expanded)
            content.setVisible(expanded)
        self.setProperty(_COLLAPSED_PROPERTY, not expanded)
        _repolish(self)
        _repolish(self._header)


def _repolish(widget: QWidget) -> None:
    """Re-evaluate the stylesheet after a dynamic property changed."""
    style = widget.style()
    if style is None:
        return
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


# -----------------------------
# Working with wrapped group boxes
# -----------------------------
def group_base_title(group: QGroupBox) -> str:
    """Return a section's title, whether or not it has been wrapped."""
    base = group.property(_BASE_TITLE_PROPERTY)
    if base:
        return str(base)
    return str(group.title() or "")


def section_for(widget: QWidget):
    """Return the section a widget lives in, or None when it is not in one."""
    node = widget
    while node is not None:
        if isinstance(node, CollapsibleSection):
            return node
        node = node.parentWidget()
    return None


def is_group_expanded(group: QGroupBox) -> bool:
    """Whether a section is open. An unwrapped widget counts as open."""
    section = section_for(group)
    return True if section is None else section.isExpanded()


def set_group_expanded(group: QGroupBox, expanded: bool) -> None:
    """Expand or collapse the section holding ``group``."""
    section = section_for(group)
    if section is not None:
        section.setExpanded(expanded)


def collapsible_sections(container: QWidget) -> list:
    """Return a container's sections, in layout order."""
    return list(container.findChildren(CollapsibleSection, options=Qt.FindDirectChildrenOnly))


def collapsible_groups(container: QWidget) -> list:
    """Return the group boxes a container's sections wrap, in layout order."""
    groups = []
    for section in collapsible_sections(container):
        content = section.contentWidget()
        if isinstance(content, QGroupBox):
            groups.append(content)
    if groups:
        return groups
    # Not wrapped yet: fall back to the container's own direct children.
    return list(container.findChildren(QGroupBox, options=Qt.FindDirectChildrenOnly))


def make_groups_collapsible(
    container: QWidget,
    *,
    on_expand=None,
    settings=None,
    settings_key: str = "",
) -> list:
    """Wrap every direct-child group box of ``container`` in a section card.

    The group box keeps its identity and its inner layout; it loses only its own
    title and frame, which the section's header row now provides.

    ``on_expand`` is called after a section is opened, for windows that gate
    controls on application state and need to re-derive that gating.
    """
    layout = container.layout()
    if layout is None:
        return []

    stored = _load_states(settings, settings_key)

    # Collect first: the layout is rewritten as we go.
    pending = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget() if item is not None else None
        if isinstance(widget, QGroupBox):
            pending.append((index, widget))

    sections = []
    for index, group in pending:
        title = group_base_title(group)
        if not title:
            continue

        group.setProperty(_BASE_TITLE_PROPERTY, title)
        group.setProperty(_SECTION_BODY_PROPERTY, True)
        group.setTitle("")
        group.setFlat(True)
        # Qt evaluates dynamic properties when a widget is polished, and this
        # group was polished long ago. Without this the "sectionBody" rule never
        # applies and the group keeps painting the frame the app stylesheet
        # gives every QGroupBox — a second card inside the section's own.
        _repolish(group)

        section = CollapsibleSection(title, container)
        layout.removeWidget(group)
        section.setContentWidget(group)
        layout.insertWidget(index, section)

        section.setExpanded(bool(stored.get(title, True)), notify=False)
        section.toggled.connect(
            lambda expanded, s=section: _on_section_toggled(
                s,
                expanded,
                on_expand=on_expand,
                settings=settings,
                settings_key=settings_key,
                container=container,
            )
        )
        sections.append(section)

    return sections


def _on_section_toggled(section, expanded, *, on_expand, settings, settings_key, container) -> None:
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
        section.title(): bool(section.isExpanded())
        for section in collapsible_sections(container)
        if section.title()
    }
    try:
        settings.setValue(settings_key, json.dumps(payload))
    except (TypeError, ValueError):
        pass
