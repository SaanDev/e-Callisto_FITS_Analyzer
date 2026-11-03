"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Persistent on-disk cache for e-CALLISTO archive downloads.

Preview, Import, Compare and Download all resolve remote URLs through
:func:`fetch_cached`, so a given archive file is pulled from its server at most
once.  Layout mirrors the sibling ``sunpy_cache`` / ``learmonth_cache`` /
``swaves_cache`` folders under the application data location.

The URL says which server a file came from.  e-CALLISTO files live directly under
the cache root; those of any other server under ``sources/<source id>/``, so a
file of the same name on two servers stays two entries and a cached path still
says where it came from.
"""

from __future__ import annotations

import gzip
from io import BytesIO
import os
import shutil
import time
from pathlib import Path
from urllib.parse import unquote, urlparse
import uuid
import zlib

from PySide6.QtCore import QSettings, QStandardPaths

from src.backend.radio.callisto_archive import (
    ARCHIVE_SOURCES,
    DEFAULT_SOURCE_ID,
    DOWNLOAD_TIMEOUT,
    build_archive_session,
    source_id_for_url,
    source_label,
)
from src.backend.radio.callisto_naming import parse_callisto_archive_filename
from src.version import APP_NAME, APP_ORG

CACHE_DIR_NAME = "callisto_cache"
SOURCES_DIR_NAME = "sources"
UNSORTED_DIR_NAME = "_unsorted"
DEFAULT_MAX_CACHE_BYTES = 2 * 1024 ** 3
CACHE_LIMIT_SETTING_KEY = "callisto/cache_max_bytes"
_CHUNK_SIZE = 1024 * 128
#: A ``.part`` file this old was left by a crash, not by a running download.
_STALE_PART_SECONDS = 24 * 3600


class InvalidArchiveFile(ValueError):
    """A server's response is not the complete, readable FITS file that was asked for."""


def _app_data_root() -> Path:
    app_data = str(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "").strip()
    if not app_data:
        app_data = str(Path.home() / ".local" / "share" / "e-callisto-fits-analyzer")
    return Path(app_data)


def cache_root() -> Path:
    """Return the cache directory, creating it when missing."""
    root = _app_data_root() / CACHE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _make_settings() -> QSettings:
    """Factory kept separate so tests can redirect the settings store."""
    return QSettings(APP_ORG, APP_NAME)


def max_cache_bytes() -> int:
    try:
        raw = _make_settings().value(CACHE_LIMIT_SETTING_KEY, DEFAULT_MAX_CACHE_BYTES)
        limit = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CACHE_BYTES
    return limit if limit > 0 else DEFAULT_MAX_CACHE_BYTES


def set_max_cache_bytes(num_bytes: int) -> None:
    limit = max(1, int(num_bytes))
    settings = _make_settings()
    settings.setValue(CACHE_LIMIT_SETTING_KEY, limit)
    settings.sync()


def filename_from_url(url: str) -> str:
    """Return the basename an archive URL points at."""
    path = urlparse(str(url or "")).path
    return os.path.basename(unquote(path)).strip()


def cache_path_for(url: str, filename: str = "") -> Path:
    """Return the cache location for an archive file.

    ``<root>/<STATION>/<date>/<name>`` for e-CALLISTO, with ``sources/<source id>/``
    after the root for any other server.
    """
    name = str(filename or "").strip() or filename_from_url(url)
    if not name:
        raise ValueError(f"Cannot derive a cache filename from: {url!r}")

    try:
        station, observed_at, _focus = parse_callisto_archive_filename(name)
        subdir = Path(station) / observed_at.strftime("%Y-%m-%d")
    except ValueError:
        subdir = Path(UNSORTED_DIR_NAME)

    source_id = source_id_for_url(url)
    if source_id != DEFAULT_SOURCE_ID:
        subdir = Path(SOURCES_DIR_NAME) / source_id / subdir
    return cache_root() / subdir / name


def source_id_for_path(path) -> str:
    """Return the server a local FITS file came from.

    Only the cache records that. Any other file, such as one saved to a folder of
    the user's, has no source information and keeps the default server.
    """
    parts = [part.casefold() for part in Path(str(path or "")).parts]
    known = {source.id.casefold(): source.id for source in ARCHIVE_SOURCES}
    for index in range(len(parts) - 2):
        if parts[index] == CACHE_DIR_NAME and parts[index + 1] == SOURCES_DIR_NAME:
            return known.get(parts[index + 2], DEFAULT_SOURCE_ID)
    return DEFAULT_SOURCE_ID


def cached_file(url: str, filename: str = "") -> Path | None:
    """Return the cached path when a usable copy exists, refreshing its mtime."""
    try:
        target = cache_path_for(url, filename)
    except ValueError:
        return None

    try:
        if target.is_file() and target.stat().st_size > 0:
            os.utime(target, None)
            return target
    except OSError:
        return None
    return None


def validate_fits_download(path) -> None:
    """Raise :class:`InvalidArchiveFile` unless ``path`` holds a readable FITS file.

    Reading the whole gzip stream checks its CRC, which catches a truncated
    transfer; the FITS check rejects what a server sends in place of a file, such
    as an HTML error page answered with status 200. Data arrays are not decoded.
    """
    from astropy.io import fits

    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError, zlib.error) as exc:
            raise InvalidArchiveFile(f"the gzip data is damaged or incomplete ({exc})") from exc
    if not raw.startswith(b"SIMPLE  ="):
        raise InvalidArchiveFile("the server did not send a FITS file")
    try:
        with fits.open(BytesIO(raw), memmap=False, lazy_load_hdus=False) as hdul:
            if not len(hdul):
                raise InvalidArchiveFile("the FITS file has no data units")
    except InvalidArchiveFile:
        raise
    except Exception as exc:
        raise InvalidArchiveFile(f"the FITS structure cannot be read ({exc})") from exc


def fetch_cached(
    url: str,
    filename: str = "",
    *,
    session=None,
    timeout: float = DOWNLOAD_TIMEOUT,
    progress_cb=None,
) -> tuple[Path, bool]:
    """Return ``(local_path, was_cached)`` for an archive URL, downloading on a miss.

    A new transfer becomes a cache entry only once it passes
    :func:`validate_fits_download`. Each transfer writes its own ``.part`` file, so
    concurrent requests for one file cannot corrupt each other.
    """
    target = cache_path_for(url, filename)

    hit = cached_file(url, filename)
    if hit is not None:
        if callable(progress_cb):
            progress_cb(1.0, f"Using cached {target.name}")
        return hit, True

    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f"{target.name}.{uuid.uuid4().hex[:12]}.part")

    owns_session = session is None
    if owns_session:
        session = build_archive_session()

    try:
        with session.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            try:
                total = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0

            written = 0
            with open(part, "wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if callable(progress_cb):
                        # Unknown-length responses still need callbacks so
                        # callers can update activity and cancel transfers.
                        fraction = min(1.0, written / total) if total > 0 else 0.0
                        progress_cb(fraction, f"Downloading {target.name}")

            # With a transfer encoding, Content-Length counts the encoded bytes.
            if total > 0 and written != total and not response.headers.get("Content-Encoding"):
                raise InvalidArchiveFile(f"received {written} of {total} bytes")

        validate_fits_download(part)
        try:
            os.replace(part, target)
        except PermissionError:
            # Windows refuses to replace a file that is open: a concurrent request
            # for this file finished first and its copy is being read.
            if not target.is_file():
                raise
            part.unlink(missing_ok=True)
    except InvalidArchiveFile as exc:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise InvalidArchiveFile(
            f"{source_label(source_id_for_url(url))} sent an unusable copy of {target.name}: {exc}"
        ) from exc
    except BaseException:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        if owns_session:
            session.close()

    if callable(progress_cb):
        progress_cb(1.0, f"Downloaded {target.name}")
    return target, False


def _cached_files() -> list[Path]:
    """Completed cache entries; in-flight ``.part`` writes are deliberately excluded
    so a concurrent eviction cannot delete a download that is still being written."""
    try:
        return [
            path
            for path in cache_root().rglob("*")
            if path.is_file() and not path.name.endswith(".part")
        ]
    except OSError:
        return []


def cache_size_bytes() -> int:
    total = 0
    for path in _cached_files():
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def format_size(num_bytes: int) -> str:
    size = float(max(0, int(num_bytes)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _prune_empty_dirs() -> None:
    root = cache_root()
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.is_dir():
            continue
        try:
            next(path.iterdir())
        except StopIteration:
            try:
                path.rmdir()
            except OSError:
                continue
        except OSError:
            continue


def _remove_stale_parts() -> None:
    cutoff = time.time() - _STALE_PART_SECONDS
    try:
        parts = list(cache_root().rglob("*.part"))
    except OSError:
        return
    for path in parts:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def enforce_cache_limit(max_bytes: int | None = None) -> int:
    """Evict oldest-first until the cache fits under the limit; return bytes freed."""
    limit = max_cache_bytes() if max_bytes is None else max(0, int(max_bytes))
    _remove_stale_parts()

    entries = []
    total = 0
    for path in _cached_files():
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
        total += stat.st_size

    if total <= limit:
        return 0

    entries.sort(key=lambda item: item[0])
    freed = 0
    for _mtime, size, path in entries:
        if total - freed <= limit:
            break
        try:
            path.unlink()
        except OSError:
            continue
        freed += size

    _prune_empty_dirs()
    return freed


def clear_cache() -> int:
    """Delete every cached file; return the number of entries that could not be removed."""
    root = cache_root()
    errors = 0
    try:
        for child in root.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                errors += 1
    except OSError:
        pass
    root.mkdir(parents=True, exist_ok=True)
    return errors
