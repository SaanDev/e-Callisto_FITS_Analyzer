import os
import sys

from PySide6.QtCore import QObject, Signal, QSettings
from PySide6.QtGui import QPalette, QColor, QGuiApplication, QIcon
from PySide6.QtCore import Qt


class AppTheme(QObject):
    """
    Global theme manager:
      - Modes: system / light / dark
      - Applies QPalette + a small QSS to the whole QApplication
      - Emits themeChanged(dark: bool) so windows can refresh plots and icons
      - Provides icon(name.svg) that picks assets/icons or assets/icons_dark
      - Provides apply_mpl(fig, ax, cbar=None) to restyle matplotlib for dark UI
    """
    themeChanged = Signal(bool)

    def __init__(self, app, org_name="SaanDev", app_name="e-CALLISTO FITS Analyzer"):
        super().__init__()
        self._app = app
        self._settings = QSettings(org_name, app_name)
        self._mode = str(self._settings.value("ui/theme_mode", "system")).lower().strip()
        if self._mode not in ("system", "light", "dark"):
            self._mode = "system"

        self._dark = None
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
        if dark == self._dark:
            return

        self._dark = dark

        if dark:
            self._app.setPalette(self._dark_palette())
            self._app.setStyleSheet(self._dark_qss())
        else:
            self._app.setPalette(self._app.style().standardPalette())
            self._app.setStyleSheet("")  # keep light mode native-ish

        self.themeChanged.emit(bool(self._dark))

    def _on_system_scheme_changed(self, *_):
        if self._mode == "system":
            self.apply()

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
        # Keep this small. Palette does most of the work.
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
