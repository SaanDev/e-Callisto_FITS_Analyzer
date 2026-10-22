"""Check a published release before announcing it.

From v3.2.0 one tag carries the full app's three installers and Lite's three
(``-lite`` in their names). Copies of the full app up to 3.1.x cannot tell
them apart: their update check downloads the first asset GitHub lists with
the right extension. So upload the full app's installers first, then Lite's,
and run this before announcing the release:

    python packaging/check_release_assets.py v3.2.0

It reads the release from GitHub's API (no token needed for a public
repository) and checks that

- the six expected installers are there, named as the update checks expect;
- for each platform, the full app's installer is listed before Lite's, so
  3.1.x copies download the full app;
- today's update checks (``src/backend/services/update_checker.py`` for the
  full app, ``lite/crates/callisto-core/src/updates.rs`` for Lite) each pick
  their own installer;
- the release title names either no platform or all three, so neither
  checker scores it down for one platform.

It exits with status 1 if anything is wrong.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.backend.services import update_checker  # noqa: E402

EXTENSIONS = {"Windows": ".exe", "Linux": ".deb", "Darwin": ".dmg"}


def expected_names(version: str) -> dict[str, tuple[str, str]]:
    """Per platform: (the full app's installer, Lite's)."""
    return {
        "Windows": (f"e-CALLISTO_FITS_Analyzer_v{version}_Setup.exe", f"e-CALLISTO_FITS_Analyzer_v{version}-lite_Setup.exe"),
        "Linux": (f"e-callisto-fits-analyzer_{version}_amd64.deb", f"e-callisto-fits-analyzer_{version}-lite_amd64.deb"),
        "Darwin": (f"e-callisto-fits-analyzer_{version}_macOS_arm64.dmg", f"e-callisto-fits-analyzer_{version}-lite_macOS_arm64.dmg"),
    }


def old_checker_pick(assets: list[dict], system: str) -> str | None:
    """What the 3.1.x update check downloads: the first asset with the extension."""
    for ext in update_checker._asset_extension_priority(update_checker._platform_family(system)):
        for a in assets:
            if str(a.get("name") or "").lower().endswith(ext):
                return a.get("name")
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    tag = sys.argv[1].strip()
    version = tag.lstrip("vV")
    url = f"{update_checker.GITHUB_API_BASE}/releases/tags/{tag}"
    resp = requests.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=15)
    if resp.status_code != 200:
        print(f"Could not read release {tag}: HTTP {resp.status_code}")
        return 1
    release = resp.json()
    assets = release.get("assets") or []
    names = [a.get("name") for a in assets]
    problems: list[str] = []

    print(f"Release {tag}: {release.get('name')!r}, {len(assets)} assets, in the order GitHub lists them:")
    for n in names:
        print(f"  {n}")

    if release.get("draft"):
        problems.append("The release is still a draft.")
    if bool(release.get("prerelease")) != bool(re.search(r"\d(?:[-_.]?(?:alpha|beta|preview|pre|rc|dev))", version, re.I)):
        problems.append("The pre-release flag does not match the tag (pre-releases are tagged like v3.3.0-beta).")

    markers = update_checker._explicit_platform_markers(update_checker._release_text(release))
    if markers and len(markers) < 3:
        problems.append(f"The title or tag names only {sorted(markers)}: name all three platforms or none.")

    for system, (main_name, lite_name) in expected_names(version).items():
        for n in (main_name, lite_name):
            if n not in names:
                problems.append(f"Missing {n}.")
        old = old_checker_pick(assets, system)
        if old != main_name:
            problems.append(f"{system}: copies up to 3.1.x would download {old!r}, not {main_name!r}. "
                            "Delete Lite's installer and upload it again after the full app's.")
        new = update_checker.select_download_url(assets, system_name=system)
        if new is None or not new.endswith("/" + main_name):
            problems.append(f"{system}: the full app's update check picks {new!r}.")
        lite = [a for a in assets if update_checker.is_lite_asset(str(a.get("name") or ""))
                and str(a.get("name") or "").lower().endswith(EXTENSIONS[system])]
        if not lite or lite[0].get("name") != lite_name:
            problems.append(f"{system}: Lite's update check would pick {lite[0].get('name') if lite else None!r}.")

    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nAll good: every update check picks its own installer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
