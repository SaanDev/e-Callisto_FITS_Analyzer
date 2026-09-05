"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

"""
dmgbuild settings for the macOS disk image.

Invoked by build_macos_dmg.sh as:
    dmgbuild -s src/Installation/dmg_settings.py \
             -D app=dist/"e-Callisto FITS Analyzer.app" \
             "e-CALLISTO FITS Analyzer 3.0.0" out.dmg

`defines` is supplied by dmgbuild from the -D flags.
"""

import os.path

application = defines.get("app")  # noqa: F821 - injected by dmgbuild
if not application:
    raise SystemExit("dmg_settings.py requires -D app=/path/to/App.app")

appname = os.path.basename(application)
icon_file = defines.get("icon", "")  # noqa: F821 - injected by dmgbuild

format = "UDZO"


def _tree_size(path):
    """Bytes needed to hold `path`, counting per-entry filesystem overhead."""
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        # Directory entries and inodes are not free, and this bundle has tens of
        # thousands of small files.
        total += 4096 * (len(dirs) + 1)
        for name in files:
            try:
                total += max(os.lstat(os.path.join(root, name)).st_size, 4096)
            except OSError:
                continue
    return total


# Leaving `size` as None makes dmgbuild guess, and its guess is short for a
# bundle this large: the writable image fills up mid-copy and ditto fails with
# "No space left on device". Size it explicitly with headroom instead. This is
# the temporary read-write image only; the delivered UDZO image is compressed
# down to whatever the payload actually needs.
size = "{}M".format(int(_tree_size(application) / (1024 * 1024) * 1.3) + 256)

files = [application]
symlinks = {"Applications": "/Applications"}

# Drag-to-install layout: app on the left, /Applications alias on the right.
default_view = "icon-view"
arrange_by = None
grid_offset = (0, 0)
grid_spacing = 100
scroll_position = (0, 0)
label_pos = "bottom"
text_size = 16
icon_size = 128

window_rect = ((200, 200), (640, 400))
background = "builtin-arrow"

icon_locations = {
    appname: (160, 190),
    "Applications": (480, 190),
}

show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False

if icon_file and os.path.exists(icon_file):
    badge_icon = icon_file
