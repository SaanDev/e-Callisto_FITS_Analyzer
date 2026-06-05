"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


application = "dist/e-Callisto FITS Analyzer.app"
volume_name = "e-Callisto FITS Analyzer"
format = "UDZO"
# The packaged macOS app bundle is currently about 1.9 GB. dmgbuild's automatic
# estimate can under-size the writable staging image, causing ditto to fail with
# "No space left on device" while copying the app into the mounted DMG.
size = "3g"
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
