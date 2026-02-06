"""
e-CALLISTO FITS Analyzer
Version 1.7.7
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

application = "dist/e-Callisto FITS Analyzer.app"
volume_name = "e-Callisto FITS Analyzer"
format = "UDZO"
size = None
# What files go inside the DMG
files = {
    application: "e-Callisto FITS Analyzer.app",
}

# Symlink to Applications folder
symlinks = {
    "Applications": "/Applications",
}

# Icon positions inside the DMG window
icon_locations = {
    "e-Callisto FITS Analyzer.app": (130, 120),
    "Applications": (500, 120),
}
