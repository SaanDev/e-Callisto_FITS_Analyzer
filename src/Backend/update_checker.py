"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from typing import Any

import requests

GITHUB_REPO = "SaanDev/e-Callisto_FITS_Analyzer"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"
GITHUB_RELEASES_LIST_URL = f"{GITHUB_API_BASE}/releases?per_page=50"

PLATFORM_WINDOWS = "windows"
PLATFORM_MACOS = "macos"
PLATFORM_LINUX = "linux"
PLATFORM_UNKNOWN = "unknown"


@dataclass(frozen=True)
class UpdateCheckResult:
    status: str
    current_version: str
    latest_version: str | None = None
    release_name: str | None = None
    release_url: str | None = None
    download_url: str | None = None
    published_at: str | None = None
    notes: str | None = None
    error: str | None = None

    @property
    def update_available(self) -> bool:
        return self.status == "update_available"

    @property
    def is_error(self) -> bool:
        return self.status == "error"


def _extract_version_text(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = text.lstrip("vV")
    match = re.search(r"\d+(?:\.\d+)*", text)
    if not match:
        return None
    return match.group(0)


def normalize_version(version: str) -> tuple[int, ...]:
    extracted = _extract_version_text(version or "")
    if not extracted:
        return ()
    return tuple(int(part) for part in extracted.split("."))


def is_newer_version(current_version: str, latest_version: str) -> bool:
    current = normalize_version(current_version)
    latest = normalize_version(latest_version)
    if not current or not latest:
        return False
    n = max(len(current), len(latest))
    current = current + (0,) * (n - len(current))
    latest = latest + (0,) * (n - len(latest))
    return latest > current


def _platform_family(system_name: str | None = None) -> str:
    text = (system_name or platform.system()).strip().lower()
    if text.startswith("win"):
        return PLATFORM_WINDOWS
    if text.startswith("darwin") or text.startswith("mac") or "osx" in text:
        return PLATFORM_MACOS
    if text.startswith("linux"):
        return PLATFORM_LINUX
    return PLATFORM_UNKNOWN


def _asset_extension_priority(platform_family: str) -> list[str]:
    if platform_family == PLATFORM_WINDOWS:
        return [".exe", ".msi", ".zip"]
    if platform_family == PLATFORM_MACOS:
        return [".dmg", ".pkg", ".zip"]
    if platform_family == PLATFORM_LINUX:
        return [".deb", ".appimage", ".tar.gz", ".zip"]
    return [".exe", ".dmg", ".deb", ".appimage", ".pkg", ".zip", ".tar.gz"]


def select_download_url(assets: list[dict[str, Any]], system_name: str | None = None) -> str | None:
    if not assets:
        return None

    platform_family = _platform_family(system_name)
    extension_priority = _asset_extension_priority(platform_family)

    lowered_assets: list[tuple[str, str]] = []
    for asset in assets:
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        if name and url:
            lowered_assets.append((name.lower(), url))

    for ext in extension_priority:
        for name, url in lowered_assets:
            if name.endswith(ext):
                return url

    if platform_family != PLATFORM_UNKNOWN:
        return None

    for _, url in lowered_assets:
        return url
    return None


def _github_get_json(url: str, timeout: float) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "e-callisto-fits-analyzer-update-check",
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _is_stable_release(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if bool(payload.get("draft")):
        return False
    if bool(payload.get("prerelease")):
        return False

    tag = str(payload.get("tag_name") or "").strip()
    name = str(payload.get("name") or "").strip()
    return bool(_extract_version_text(tag) or _extract_version_text(name))


def _release_version(payload: dict[str, Any]) -> tuple[int, ...]:
    tag = str(payload.get("tag_name") or "").strip()
    name = str(payload.get("name") or "").strip()
    raw = _extract_version_text(tag) or _extract_version_text(name) or ""
    return normalize_version(raw)


def _release_text(payload: dict[str, Any]) -> str:
    tag = str(payload.get("tag_name") or "").strip().lower()
    name = str(payload.get("name") or "").strip().lower()
    return f"{tag} {name}".strip()


def _explicit_platform_markers(text: str) -> set[str]:
    markers: set[str] = set()
    if re.search(r"\bwindows\b", text):
        markers.add(PLATFORM_WINDOWS)
    if re.search(r"\bmac\s*os\b|\bmacos\b|\bdarwin\b|\bosx\b", text):
        markers.add(PLATFORM_MACOS)
    if re.search(r"\blinux\b|\bubuntu\b|\bdebian\b", text):
        markers.add(PLATFORM_LINUX)
    return markers


def _release_platform_score(payload: dict[str, Any], platform_family: str) -> tuple[int, int]:
    """
    Returns:
      - tag_score: how well release metadata (tag/name) matches platform
      - asset_score: whether a platform-specific installer asset exists
    """
    text = _release_text(payload)
    markers = _explicit_platform_markers(text)
    if platform_family == PLATFORM_UNKNOWN:
        tag_score = 0
    elif platform_family in markers:
        tag_score = 2
    elif markers:
        tag_score = -2
    else:
        tag_score = 0

    assets_raw = payload.get("assets") or []
    assets = assets_raw if isinstance(assets_raw, list) else []
    asset_score = 1 if select_download_url(assets, system_name=platform_family) else 0
    return tag_score, asset_score


def _pad_version(version: tuple[int, ...], width: int = 5) -> tuple[int, ...]:
    return version + (0,) * max(0, width - len(version))


def _pick_latest_stable_release_for_platform(timeout: float, system_name: str | None) -> dict[str, Any]:
    releases = _github_get_json(GITHUB_RELEASES_LIST_URL, timeout=timeout)
    if not isinstance(releases, list):
        raise ValueError("Unexpected release list payload from GitHub API.")

    stable_releases = [r for r in releases if _is_stable_release(r)]
    if not stable_releases:
        raise ValueError("No stable release found in GitHub release list.")

    platform_family = _platform_family(system_name)
    scored: list[tuple[tuple[int, ...], int, int, str, dict[str, Any]]] = []
    for release in stable_releases:
        version = _release_version(release)
        if not version:
            continue
        tag_score, asset_score = _release_platform_score(release, platform_family)
        published_at = str(release.get("published_at") or "")
        scored.append((_pad_version(version), tag_score, asset_score, published_at, release))

    if not scored:
        raise ValueError("No parseable stable release version found.")

    if platform_family != PLATFORM_UNKNOWN:
        filtered = [item for item in scored if (item[1] > 0 or item[2] > 0)]
        if not filtered:
            raise ValueError(f"No release found for platform '{platform_family}'.")
    else:
        filtered = scored

    filtered.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    return filtered[0][-1]


def check_for_updates(
    current_version: str,
    timeout: float = 8.0,
    system_name: str | None = None,
) -> UpdateCheckResult:
    try:
        platform_family = _platform_family(system_name)
        release = _pick_latest_stable_release_for_platform(timeout=timeout, system_name=platform_family)

        latest_version = _extract_version_text(str(release.get("tag_name") or "")) or _extract_version_text(
            str(release.get("name") or "")
        )
        if not latest_version:
            raise ValueError("Could not parse release version from GitHub response.")

        release_name = str(release.get("name") or "").strip() or f"v{latest_version}"
        release_url = str(release.get("html_url") or "").strip() or None
        published_at = str(release.get("published_at") or "").strip() or None
        notes = str(release.get("body") or "").strip() or None

        assets_raw = release.get("assets") or []
        assets = assets_raw if isinstance(assets_raw, list) else []
        download_url = select_download_url(assets, system_name=platform_family) or release_url

        status = "update_available" if is_newer_version(current_version, latest_version) else "up_to_date"

        return UpdateCheckResult(
            status=status,
            current_version=current_version,
            latest_version=latest_version,
            release_name=release_name,
            release_url=release_url,
            download_url=download_url,
            published_at=published_at,
            notes=notes,
        )
    except Exception as exc:
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            error=str(exc),
        )
