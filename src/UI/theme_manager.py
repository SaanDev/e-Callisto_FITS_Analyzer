"""
e-CALLISTO FITS Analyzer
Version 2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import os
import sys

from PySide6.QtCore import QObject, Signal, QSettings
from PySide6.QtGui import QPalette, QColor, QGuiApplication, QIcon
from PySide6.QtCore import Qt
from src.version import APP_NAME, APP_ORG


class AppTheme(QObject):
    """
    Global theme manager:
      - Theme modes: system / light / dark
      - View modes: classic / modern
      - Applies QPalette + app-wide QSS to the whole QApplication
      - Emits themeChanged(dark: bool) so windows can refresh plots and icons
      - Provides icon(name.svg) that picks assets/icons or assets/icons_dark
      - Provides apply_mpl(fig, ax, cbar=None) to restyle matplotlib for dark UI
    """
    themeChanged = Signal(bool)
    viewModeChanged = Signal(str)

    def __init__(self, app, org_name=APP_ORG, app_name=APP_NAME):
        super().__init__()
        self._app = app
        self._settings = QSettings(org_name, app_name)
        self._mode = str(self._settings.value("ui/theme_mode", "system")).lower().strip()
        if self._mode not in ("system", "light", "dark"):
            self._mode = "system"
        self._view_mode = self._normalize_view_mode(self._settings.value("ui/view_mode", "modern"))

        self._dark = None
        self._last_applied_signature = None
        self._assets_dir = self._find_assets_dir()

        # Listen to system scheme changes (works on macOS and many Qt6 setups)
        try:
            hints = QGuiApplication.styleHints()
            if hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(self._on_system_scheme_changed)
        except Exception:
            pass

        self.apply()

    # ---------- public API ----------

    def mode(self) -> str:
        return self._mode

    def view_mode(self) -> str:
        return self._view_mode

    def is_dark(self) -> bool:
        return bool(self._dark)

    def set_mode(self, mode: str):
        mode = (mode or "").lower().strip()
        if mode not in ("system", "light", "dark"):
            return
        if mode == self._mode:
            return
        self._mode = mode
        self._settings.setValue("ui/theme_mode", self._mode)
        self.apply()

    def set_view_mode(self, mode: str):
        mode = self._normalize_view_mode(mode)
        if mode == self._view_mode:
            return
        self._view_mode = mode
        self._settings.setValue("ui/view_mode", self._view_mode)
        self.apply()
        self.viewModeChanged.emit(self._view_mode)

    def icon(self, filename: str) -> QIcon:
        # filename: "open.svg", "download.svg", etc
        folder = "icons_dark" if self.is_dark() else "icons"
        p = os.path.join(self._assets_dir, folder, filename)

        # Fallbacks in case one path is missing
        if not os.path.exists(p):
            p2 = os.path.join(self._assets_dir, "icons", filename)
            if os.path.exists(p2):
                p = p2

        return QIcon(p)

    def apply_mpl(self, fig, ax, cbar=None):
        """
        Call after any plot redraw that changes axes/labels/colorbar.
        Ensures dark theme stays readable.
        """
        pal = self._app.palette()
        window_bg = pal.color(QPalette.Window)
        base_bg = pal.color(QPalette.Base)
        fg = pal.color(QPalette.WindowText)
        mid = pal.color(QPalette.Mid)

        fig.patch.set_facecolor(window_bg.name())
        ax.set_facecolor(base_bg.name())

        ax.tick_params(colors=fg.name(), which="both")
        ax.xaxis.label.set_color(fg.name())
        ax.yaxis.label.set_color(fg.name())
        ax.title.set_color(fg.name())

        for spine in ax.spines.values():
            spine.set_color(fg.name())

        ax.grid(color=mid.name(), alpha=0.25)

        if cbar is not None:
            try:
                cbar.ax.tick_params(colors=fg.name(), which="both")
                for t in cbar.ax.get_yticklabels():
                    t.set_color(fg.name())
                cbar.outline.set_edgecolor(fg.name())
                if hasattr(cbar, "ax") and hasattr(cbar.ax, "yaxis"):
                    cbar.ax.yaxis.label.set_color(fg.name())
            except Exception:
                pass

        if getattr(fig, "canvas", None) is not None:
            fig.canvas.draw_idle()

    # ---------- internals ----------

    def apply(self):
        # Use Fusion so palettes behave consistently across platforms.
        self._app.setStyle("Fusion")

        dark = self._effective_dark()
        signature = (dark, self._view_mode)

        if dark:
            self._app.setPalette(self._dark_palette())
        else:
            self._app.setPalette(self._app.style().standardPalette())
        self._app.setStyleSheet(self._compose_qss(dark))

        self._dark = dark
        if signature != self._last_applied_signature:
            self._last_applied_signature = signature
            self.themeChanged.emit(bool(self._dark))

    def _on_system_scheme_changed(self, *_):
        if self._mode == "system":
            self.apply()

    def _normalize_view_mode(self, mode) -> str:
        text = str(mode or "").strip().lower()
        if text in {"classic", "modern"}:
            return text
        return "modern"

    def _effective_dark(self) -> bool:
        if self._mode == "dark":
            return True
        if self._mode == "light":
            return False
        return self._system_is_dark()

    def _system_is_dark(self) -> bool:
        # Preferred: Qt6 color scheme
        try:
            hints = QGuiApplication.styleHints()
            if hasattr(hints, "colorScheme"):
                scheme = hints.colorScheme()
                if scheme == Qt.ColorScheme.Dark:
                    return True
                if scheme == Qt.ColorScheme.Light:
                    return False
        except Exception:
            pass

        # Fallback: infer from current palette window lightness
        col = self._app.palette().color(QPalette.Window)
        return col.lightness() < 128

    def _dark_palette(self) -> QPalette:
        pal = QPalette()

        window = QColor("#2b2b2b")
        base = QColor("#1f1f1f")
        alt = QColor("#2b2b2b")
        text = QColor("#f0f0f0")
        button = QColor("#353535")
        highlight = QColor("#0a84ff")  # macOS-like blue

        pal.setColor(QPalette.Window, window)
        pal.setColor(QPalette.WindowText, text)
        pal.setColor(QPalette.Base, base)
        pal.setColor(QPalette.AlternateBase, alt)
        pal.setColor(QPalette.Text, text)
        pal.setColor(QPalette.Button, button)
        pal.setColor(QPalette.ButtonText, text)
        pal.setColor(QPalette.ToolTipBase, QColor("#3a3a3a"))
        pal.setColor(QPalette.ToolTipText, text)
        pal.setColor(QPalette.Highlight, highlight)
        pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        pal.setColor(QPalette.Link, highlight)
        pal.setColor(QPalette.Mid, QColor("#5a5a5a"))

        return pal

    def _dark_qss(self) -> str:
        # Classic dark mode: small set of readability tweaks.
        return """
        QToolTip {
            color: #f0f0f0;
            background-color: #3a3a3a;
            border: 1px solid #5a5a5a;
        }
        QMenu::separator {
            height: 1px;
            background: #5a5a5a;
            margin: 4px 8px;
        }
        QStatusBar::item { border: none; }
        """

    def _compose_qss(self, dark: bool) -> str:
        if self._view_mode == "modern":
            return self._modern_qss(dark)
        return self._dark_qss() if dark else ""

    def _modern_qss(self, dark: bool) -> str:
        if dark:
            page_bg = "#0f151e"
            surface_bg = "#171f2b"
            surface_alt = "#202b3b"
            input_bg = "#121a25"
            border = "#314055"
            text = "#e8eef8"
            muted = "#9db0c9"
            hover = "#29364a"
            pressed = "#334760"
            accent = "#4ea3ff"
            accent_soft = "#1f3650"
            disabled = "#6f8098"
        else:
            page_bg = "#f4f7fc"
            surface_bg = "#ffffff"
            surface_alt = "#f5f9ff"
            input_bg = "#ffffff"
            border = "#d3dcea"
            text = "#202a36"
            muted = "#61758f"
            hover = "#ecf3ff"
            pressed = "#dfeaff"
            accent = "#146fda"
            accent_soft = "#e8f2ff"
            disabled = "#98a9bf"

        down_icon = self._icon_qss_url("chevron_down_small.svg", dark)
        up_icon = self._icon_qss_url("chevron_up_small.svg", dark)
        down_rule = f'image: url("{down_icon}");' if down_icon else "image: none;"
        up_rule = f'image: url("{up_icon}");' if up_icon else "image: none;"

        return f"""
        QWidget {{
            color: {text};
        }}
        QMainWindow, QDialog {{
            background-color: {page_bg};
        }}
        QWidget#top_panel {{
            background-color: {surface_alt};
            border-bottom: 1px solid {border};
        }}
        QToolTip {{
            color: {text};
            background: {surface_bg};
            border: 1px solid {border};
            padding: 5px 7px;
        }}
        QGroupBox {{
            background: {surface_bg};
            border: 1px solid {border};
            border-radius: 12px;
            margin-top: 14px;
            padding: 12px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 5px;
            color: {muted};
        }}
        QLabel {{
            color: {text};
        }}
        QLabel[section="true"] {{
            color: {muted};
            font-weight: 600;
            margin-top: 8px;
            margin-bottom: 2px;
        }}
        QLabel#SectionLabel {{
            color: {muted};
            font-weight: 600;
            margin-top: 10px;
            margin-bottom: 3px;
        }}
        QLineEdit, QTextEdit, QPlainTextEdit, QListWidget, QTableWidget, QTreeWidget,
        QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit, QTimeEdit {{
            border: 1px solid {border};
            border-radius: 10px;
            background: {input_bg};
            color: {text};
            selection-background-color: {accent};
            selection-color: #ffffff;
        }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit, QTimeEdit {{
            min-height: 32px;
            padding: 4px 10px;
        }}
        QTextEdit, QPlainTextEdit, QListWidget, QTableWidget, QTreeWidget {{
            min-height: 0px;
            padding: 6px 8px;
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus,
        QTableWidget:focus, QTreeWidget:focus, QComboBox:focus, QSpinBox:focus,
        QDoubleSpinBox:focus, QDateEdit:focus, QDateTimeEdit:focus, QTimeEdit:focus {{
            border: 1px solid {accent};
            background: {input_bg};
        }}
        QComboBox, QDateEdit, QDateTimeEdit, QTimeEdit {{
            padding-right: 32px;
        }}
        QComboBox::drop-down,
        QDateEdit::drop-down,
        QDateTimeEdit::drop-down,
        QTimeEdit::drop-down {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 26px;
            border-left: 1px solid {border};
            border-top-right-radius: 10px;
            border-bottom-right-radius: 10px;
            background: {surface_alt};
        }}
        QComboBox::drop-down:hover,
        QDateEdit::drop-down:hover,
        QDateTimeEdit::drop-down:hover,
        QTimeEdit::drop-down:hover {{
            background: {hover};
        }}
        QComboBox::down-arrow,
        QDateEdit::down-arrow,
        QDateTimeEdit::down-arrow,
        QTimeEdit::down-arrow {{
            width: 12px;
            height: 12px;
            {down_rule}
        }}
        QComboBox QAbstractItemView {{
            border: 1px solid {border};
            border-radius: 10px;
            background: {surface_bg};
            color: {text};
            selection-background-color: {accent};
            selection-color: #ffffff;
            outline: none;
            padding: 4px;
        }}
        QSpinBox, QDoubleSpinBox {{
            padding-right: 32px;
        }}
        QSpinBox::up-button, QDoubleSpinBox::up-button {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 26px;
            border-left: 1px solid {border};
            border-bottom: 1px solid {border};
            border-top-right-radius: 10px;
            background: {surface_alt};
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 26px;
            border-left: 1px solid {border};
            border-bottom-right-radius: 10px;
            background: {surface_alt};
        }}
        QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
        QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
            background: {hover};
        }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
            width: 12px;
            height: 12px;
            {up_rule}
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
            width: 12px;
            height: 12px;
            {down_rule}
        }}
        QDateEdit::up-button, QDateTimeEdit::up-button, QTimeEdit::up-button,
        QDateEdit::down-button, QDateTimeEdit::down-button, QTimeEdit::down-button {{
            background: {surface_alt};
            border-left: 1px solid {border};
            width: 26px;
        }}
        QDateEdit::up-arrow, QDateTimeEdit::up-arrow, QTimeEdit::up-arrow {{
            width: 12px;
            height: 12px;
            {up_rule}
        }}
        QDateEdit::down-arrow, QDateTimeEdit::down-arrow, QTimeEdit::down-arrow {{
            width: 12px;
            height: 12px;
            {down_rule}
        }}
        QPushButton {{
            min-height: 32px;
            border: 1px solid {border};
            border-radius: 10px;
            padding: 6px 13px;
            background: {surface_bg};
            color: {text};
        }}
        QPushButton:hover {{
            background: {hover};
        }}
        QPushButton:pressed {{
            background: {pressed};
        }}
        QPushButton:disabled {{
            color: {disabled};
            border-color: {border};
            background: {surface_alt};
        }}
        QPushButton:checked {{
            border-color: {accent};
            background: {accent_soft};
        }}
        QCheckBox, QRadioButton {{
            spacing: 7px;
            color: {text};
        }}
        QCheckBox::indicator, QRadioButton::indicator {{
            width: 15px;
            height: 15px;
        }}
        QCheckBox::indicator {{
            border: 1px solid {border};
            border-radius: 4px;
            background: {input_bg};
        }}
        QRadioButton::indicator {{
            border: 1px solid {border};
            border-radius: 7px;
            background: {input_bg};
        }}
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
            background: {accent};
            border-color: {accent};
        }}
        QSlider::groove:horizontal {{
            height: 6px;
            background: {border};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            width: 14px;
            margin: -5px 0;
            border-radius: 7px;
            background: {accent};
        }}
        QMenuBar {{
            background: {surface_bg};
            border-bottom: 1px solid {border};
            padding: 3px 7px;
        }}
        QMenuBar::item {{
            padding: 6px 10px;
            border-radius: 7px;
            background: transparent;
        }}
        QMenuBar::item:selected {{
            background: {hover};
        }}
        QMenu {{
            background: {surface_bg};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 7px 16px 7px 10px;
            border-radius: 7px;
        }}
        QMenu::item:selected {{
            background: {hover};
        }}
        QMenu::separator {{
            height: 1px;
            background: {border};
            margin: 5px 8px;
        }}
        QToolBar {{
            background: {surface_bg};
            border: none;
            border-bottom: 1px solid {border};
            spacing: 6px;
            padding: 7px 8px;
        }}
        QToolButton {{
            border: 1px solid transparent;
            border-radius: 10px;
            padding: 4px;
            background: transparent;
        }}
        QToolButton:hover {{
            background: {hover};
            border-color: {border};
        }}
        QToolButton:pressed {{
            background: {pressed};
        }}
        QStatusBar {{
            background: {surface_bg};
            border-top: 1px solid {border};
        }}
        QStatusBar::item {{
            border: none;
        }}
        QProgressBar {{
            border: 1px solid {border};
            border-radius: 7px;
            background: {input_bg};
            text-align: center;
            min-height: 18px;
        }}
        QProgressBar::chunk {{
            background: {accent};
            border-radius: 6px;
        }}
        QTableView, QTableWidget {{
            gridline-color: {border};
            alternate-background-color: {surface_alt};
        }}
        QHeaderView::section {{
            background: {surface_alt};
            color: {muted};
            border: 1px solid {border};
            border-right: none;
            padding: 6px;
            font-weight: 600;
        }}
        QHeaderView::section:last {{
            border-right: 1px solid {border};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 4px 0 4px 0;
        }}
        QScrollBar::handle:vertical {{
            background: {border};
            border-radius: 5px;
            min-height: 24px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
            margin: 0 4px 0 4px;
        }}
        QScrollBar::handle:horizontal {{
            background: {border};
            border-radius: 5px;
            min-width: 24px;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
        QPushButton#SidebarToggleButton {{
            min-width: 12px;
            max-width: 12px;
            min-height: 22px;
            max-height: 22px;
            padding: 0px;
            border-radius: 6px;
        }}
        """

    def _icon_qss_url(self, filename: str, dark: bool) -> str:
        folder = "icons_dark" if dark else "icons"
        primary = os.path.join(self._assets_dir, folder, filename)
        fallback_folder = "icons" if folder == "icons_dark" else "icons_dark"
        fallback = os.path.join(self._assets_dir, fallback_folder, filename)

        path = primary if os.path.exists(primary) else (fallback if os.path.exists(fallback) else "")
        if not path:
            return ""
        # Qt stylesheets expect local filesystem paths here; file:// URLs can be
        # interpreted as relative and break arrow rendering on some platforms.
        return os.path.abspath(path).replace("\\", "/")

    def _find_assets_dir(self) -> str:
        # Tries to find "<project>/assets" by walking up from likely roots.
        roots = []

        # Running from source
        roots.append(os.path.abspath(os.path.dirname(__file__)))
        roots.append(os.path.abspath(os.getcwd()))

        # Frozen (PyInstaller)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(os.path.abspath(meipass))

        # Frozen (py2app style)
        if getattr(sys, "frozen", False):
            roots.append(os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", "Resources")))

        for r in roots:
            cur = r
            for _ in range(6):
                candidate = os.path.join(cur, "assets")
                if os.path.isdir(candidate):
                    return candidate
                cur = os.path.abspath(os.path.join(cur, ".."))

        # Last resort
        return os.path.join(os.path.abspath(os.path.dirname(__file__)), "assets")
