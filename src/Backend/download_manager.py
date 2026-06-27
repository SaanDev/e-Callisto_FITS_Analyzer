"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Byte-accurate concurrent download engine.

The legacy SDO/AIA download path went through SunPy ``Fido``/``parfive`` with
``progress=False``, so the UI could only learn that a *whole* file had finished
(never how many bytes were in flight). That made the progress bar dishonest: it
was animated by a timer rather than driven by real transfer.

This module provides a small, Qt-free download engine built directly on
``aiohttp``. It streams every file in user-controllable chunks and reports real
``(bytes_done, bytes_total, speed, eta)`` for each file and for the batch as a
whole. It also supports HTTP ``Range`` resume, cooperative cancellation, a pause
gate, and skipping files already present in the cache.

It powers two things:

* The faster direct-URL sources added later (JSOC export URLs, Helioviewer), and
* The honest aggregate progress shown for the existing VSO path, via
  :class:`CacheByteMonitor`, which watches a download directory grow when the
  bytes are produced by a downloader we do not control (parfive).

Everything here is intentionally free of PySide/Qt imports so it can be unit
tested headlessly and reused from any worker thread.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
import logging
import os
from pathlib import Path
import re
import time
from typing import Callable, Iterable, Sequence

_logger = logging.getLogger("ecallisto.sunpy")

DEFAULT_USER_AGENT = "eCallisto-FITS-Analyzer/2.7 (+https://www.e-callisto.org)"

# Download lifecycle states (kept as plain strings so they cross the Qt signal
# boundary and serialise trivially).
STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_DONE = "done"
STATUS_CACHED = "cached"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_PAUSED = "paused"

_TERMINAL_STATES = frozenset({STATUS_DONE, STATUS_CACHED, STATUS_FAILED, STATUS_CANCELLED})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DownloadItem:
    """A single file to fetch."""

    url: str
    dest: Path
    expected_size: int | None = None
    record_id: str | None = None
    label: str | None = None
    headers: dict[str, str] | None = None

    def display_name(self) -> str:
        if self.label:
            return self.label
        try:
            name = Path(self.dest).name
        except Exception:
            name = ""
        return name or self.record_id or self.url


@dataclass
class FileProgress:
    """Live progress for one :class:`DownloadItem`."""

    name: str
    status: str = STATUS_QUEUED
    bytes_done: int = 0
    bytes_total: int | None = None
    speed_bps: float = 0.0
    error: str | None = None
    path: str | None = None

    @property
    def fraction(self) -> float:
        total = self.bytes_total or 0
        if total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.bytes_done / total))

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATES


@dataclass
class AggregateProgress:
    """Snapshot of the whole batch, handed to the UI on every tick."""

    files_total: int
    files_done: int = 0
    bytes_done: int = 0
    bytes_total: int | None = None
    speed_bps: float = 0.0
    eta_seconds: float | None = None
    per_file: list[FileProgress] = field(default_factory=list)
    active_names: list[str] = field(default_factory=list)

    @property
    def fraction(self) -> float:
        total = self.bytes_total or 0
        if total > 0:
            return max(0.0, min(1.0, self.bytes_done / total))
        if self.files_total > 0:
            return max(0.0, min(1.0, self.files_done / self.files_total))
        return 0.0

    def percent(self) -> int:
        return int(round(self.fraction * 100))


@dataclass(frozen=True)
class DownloadResult:
    paths: list[str]
    errors: list[str]
    cached_count: int = 0
    cancelled: bool = False


# ---------------------------------------------------------------------------
# Size text parsing / formatting (shared with the archive layer)
# ---------------------------------------------------------------------------
_SIZE_UNITS = {
    "B": 1,
    "BYTE": 1,
    "BYTES": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "KBYTE": 1024,
    "KBYTES": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "MIB": 1024**2,
    "MBYTE": 1024**2,
    "MBYTES": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "GIB": 1024**3,
    "GBYTE": 1024**3,
    "GBYTES": 1024**3,
}

_SIZE_RE = re.compile(r"([-+]?\d*\.?\d+)\s*([A-Za-z]+)?")


def parse_size_text(text: object) -> int | None:
    """Parse an archive size string (e.g. ``"12.6 MByte"``) into bytes.

    VSO rows report sizes as free text; we use it to seed an honest total for
    the byte progress bar before any data has been transferred. Returns ``None``
    when the text carries no usable number.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        value = float(text)
        return int(value) if value > 0 else None

    raw = str(text).strip()
    if not raw:
        return None
    match = _SIZE_RE.search(raw)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    unit = (match.group(2) or "MB").strip().upper().rstrip(".")
    multiplier = _SIZE_UNITS.get(unit)
    if multiplier is None:
        # Bare unit-less numbers in VSO tables are conventionally MByte.
        multiplier = _SIZE_UNITS["MB"]
    return int(value * multiplier)


def format_bytes(num_bytes: float | None) -> str:
    if num_bytes is None:
        return "?"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def format_speed(bps: float | None) -> str:
    if not bps or bps <= 0:
        return "--"
    return f"{format_bytes(bps)}/s"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


# ---------------------------------------------------------------------------
# Speed estimation
# ---------------------------------------------------------------------------
class _SpeedMeter:
    """Sliding-window transfer-rate estimator.

    A naive ``total_bytes / elapsed`` average lags badly when a transfer ramps
    up or stalls; this keeps only the last ``window`` seconds of samples so the
    reported MB/s tracks the *current* rate, which is what an ETA needs.
    """

    def __init__(self, window: float = 3.0):
        self._window = float(window)
        self._samples: deque[tuple[float, int]] = deque()

    def update(self, total_bytes: int, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        self._samples.append((now, int(total_bytes)))
        cutoff = now - self._window
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.popleft()
        if len(self._samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        dt = t1 - t0
        if dt <= 0:
            return 0.0
        return max(0.0, (b1 - b0) / dt)


def compute_eta(bytes_done: int, bytes_total: int | None, speed_bps: float) -> float | None:
    if not bytes_total or bytes_total <= 0 or speed_bps <= 0:
        return None
    remaining = bytes_total - bytes_done
    if remaining <= 0:
        return 0.0
    return remaining / speed_bps


# ---------------------------------------------------------------------------
# Cache growth monitor (for downloaders we do not control, e.g. parfive/VSO)
# ---------------------------------------------------------------------------
class CacheByteMonitor:
    """Estimate bytes transferred by watching a directory grow.

    parfive streams VSO files itself and exposes no byte callback, so for that
    path we cannot instrument the socket. Instead we snapshot the cache before a
    download and periodically diff it: new files plus growth of existing files
    approximate bytes-on-the-wire closely enough to drive an honest bar, a real
    MB/s read-out, and an ETA. Pair it with a known ``expected_total`` (summed
    from the archive size column) for the denominator.
    """

    def __init__(self, cache_dir: str | Path, *, expected_total: int | None = None, window: float = 3.0):
        self.cache_dir = Path(cache_dir).expanduser()
        self.expected_total = expected_total if (expected_total or 0) > 0 else None
        self._baseline: dict[str, int] = self._snapshot()
        self._baseline_total = sum(self._baseline.values())
        self._meter = _SpeedMeter(window=window)
        self._last_done = 0

    def _snapshot(self) -> dict[str, int]:
        out: dict[str, int] = {}
        try:
            for entry in os.scandir(self.cache_dir):
                if entry.is_file():
                    try:
                        out[entry.path] = entry.stat().st_size
                    except OSError:
                        continue
        except (FileNotFoundError, NotADirectoryError):
            pass
        return out

    def bytes_downloaded(self) -> int:
        current = self._snapshot()
        delta = 0
        for path, size in current.items():
            base = self._baseline.get(path, 0)
            grown = size - base
            if grown > 0:
                delta += grown
        # New files account for their whole size; the loop above already covers
        # them because baseline.get(...) is 0. Monotonic guard against transient
        # shrink during rename/replace.
        self._last_done = max(self._last_done, delta)
        return self._last_done

    def sample(self, now: float | None = None) -> AggregateProgress:
        done = self.bytes_downloaded()
        speed = self._meter.update(done, now=now)
        total = self.expected_total
        if total is not None and done > total:
            total = done
        eta = compute_eta(done, total, speed)
        return AggregateProgress(
            files_total=0,
            files_done=0,
            bytes_done=done,
            bytes_total=total,
            speed_bps=speed,
            eta_seconds=eta,
        )


# ---------------------------------------------------------------------------
# aiohttp engine
# ---------------------------------------------------------------------------
class DownloadManager:
    """Concurrent, resumable, byte-accurate downloader built on aiohttp."""

    def __init__(
        self,
        *,
        max_concurrent: int = 6,
        chunk_size: int = 1 << 16,
        progress_interval: float = 0.15,
        connect_timeout: float = 30.0,
        read_timeout: float = 60.0,
        total_timeout: float | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self.max_concurrent = max(1, int(max_concurrent))
        self.chunk_size = max(8192, int(chunk_size))
        self.progress_interval = max(0.0, float(progress_interval))
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.total_timeout = total_timeout
        self.user_agent = user_agent

    # -- public, synchronous entry point -----------------------------------
    def download(
        self,
        items: Sequence[DownloadItem],
        *,
        progress_cb: Callable[[AggregateProgress], None] | None = None,
        cancel_cb: Callable[[], bool] | None = None,
        is_paused: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        """Download ``items`` and block until done. Runs its own event loop so
        it is safe to call from a QThread that has no running loop."""
        items = [it for it in items if it and it.url]
        if not items:
            return DownloadResult(paths=[], errors=[], cached_count=0, cancelled=False)

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                self._run(items, progress_cb=progress_cb, cancel_cb=cancel_cb, is_paused=is_paused)
            )
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)

    # -- async core --------------------------------------------------------
    async def _run(
        self,
        items: Sequence[DownloadItem],
        *,
        progress_cb: Callable[[AggregateProgress], None] | None,
        cancel_cb: Callable[[], bool] | None,
        is_paused: Callable[[], bool] | None,
    ) -> DownloadResult:
        import aiohttp

        progress = [FileProgress(name=it.display_name(), bytes_total=it.expected_size) for it in items]
        # Pre-mark already-cached files so the very first tick is honest.
        for idx, item in enumerate(items):
            cached_path = _existing_complete_path(item)
            if cached_path is not None:
                progress[idx].status = STATUS_CACHED
                progress[idx].bytes_done = progress[idx].bytes_total or _safe_size(cached_path)
                progress[idx].path = str(cached_path)

        meter = _SpeedMeter()
        last_emit = 0.0
        emit_lock = asyncio.Lock()

        def aggregate(now: float | None = None) -> AggregateProgress:
            now = time.monotonic() if now is None else now
            bytes_done = sum(p.bytes_done for p in progress)
            known_totals = [p.bytes_total for p in progress if p.bytes_total]
            bytes_total = sum(known_totals) if len(known_totals) == len(progress) else None
            if bytes_total is None and known_totals:
                # Partial knowledge: extrapolate so the denominator is sane.
                avg = sum(known_totals) / len(known_totals)
                bytes_total = int(avg * len(progress))
            speed = meter.update(bytes_done, now=now)
            files_done = sum(1 for p in progress if p.is_terminal)
            eta = compute_eta(bytes_done, bytes_total, speed)
            active = [p.name for p in progress if p.status == STATUS_DOWNLOADING]
            return AggregateProgress(
                files_total=len(progress),
                files_done=files_done,
                bytes_done=bytes_done,
                bytes_total=bytes_total,
                speed_bps=speed,
                eta_seconds=eta,
                per_file=[replace(p) for p in progress],
                active_names=active,
            )

        async def emit(force: bool = False) -> None:
            nonlocal last_emit
            if progress_cb is None:
                return
            now = time.monotonic()
            if not force and (now - last_emit) < self.progress_interval:
                return
            async with emit_lock:
                last_emit = now
                snapshot = aggregate(now)
            try:
                progress_cb(snapshot)
            except Exception:
                _logger.debug("download progress callback raised", exc_info=True)

        await emit(force=True)

        timeout = aiohttp.ClientTimeout(
            total=self.total_timeout,
            connect=self.connect_timeout,
            sock_read=self.read_timeout,
        )
        connector = aiohttp.TCPConnector(limit=self.max_concurrent, ssl=_ssl_context())
        semaphore = asyncio.Semaphore(self.max_concurrent)
        cancelled = {"flag": False}

        def _cancelled() -> bool:
            if cancelled["flag"]:
                return True
            if cancel_cb is not None:
                try:
                    if bool(cancel_cb()):
                        cancelled["flag"] = True
                except Exception:
                    pass
            return cancelled["flag"]

        async def worker(idx: int, item: DownloadItem, session) -> None:
            fp = progress[idx]
            if fp.status == STATUS_CACHED:
                return
            if _cancelled():
                fp.status = STATUS_CANCELLED
                return
            async with semaphore:
                if _cancelled():
                    fp.status = STATUS_CANCELLED
                    return
                await self._download_one(
                    item, fp, session,
                    emit=emit, cancelled=_cancelled, is_paused=is_paused,
                )

        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={"User-Agent": self.user_agent},
            ) as session:
                tasks = [asyncio.create_task(worker(idx, item, session)) for idx, item in enumerate(items)]
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await emit(force=True)

        paths = [p.path for p in progress if p.path and p.status in (STATUS_DONE, STATUS_CACHED)]
        errors = [f"{p.name}: {p.error}" for p in progress if p.status == STATUS_FAILED and p.error]
        cached = sum(1 for p in progress if p.status == STATUS_CACHED)
        return DownloadResult(
            paths=paths,
            errors=errors,
            cached_count=cached,
            cancelled=cancelled["flag"],
        )

    async def _download_one(
        self,
        item: DownloadItem,
        fp: FileProgress,
        session,
        *,
        emit: Callable,
        cancelled: Callable[[], bool],
        is_paused: Callable[[], bool] | None,
    ) -> None:
        import aiohttp

        dest = Path(item.dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        resume_from = _safe_size(part)

        headers = dict(item.headers or {})
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

        fp.status = STATUS_DOWNLOADING
        fp.bytes_done = resume_from
        await emit()

        try:
            async with session.get(item.url, headers=headers) as response:
                mode = "ab"
                if response.status == 200 and resume_from > 0:
                    # Server ignored the Range header; restart cleanly.
                    resume_from = 0
                    fp.bytes_done = 0
                    mode = "wb"
                elif response.status not in (200, 206):
                    response.raise_for_status()

                total = _content_total(response, resume_from)
                if total:
                    fp.bytes_total = total

                with open(part, mode) as handle:
                    async for chunk in response.content.iter_chunked(self.chunk_size):
                        if cancelled():
                            fp.status = STATUS_CANCELLED
                            return
                        await _await_unpause(is_paused, cancelled)
                        if not chunk:
                            continue
                        handle.write(chunk)
                        fp.bytes_done += len(chunk)
                        await emit()

            if cancelled():
                fp.status = STATUS_CANCELLED
                return

            os.replace(part, dest)
            fp.path = str(dest.resolve())
            fp.bytes_done = _safe_size(dest)
            if fp.bytes_total is None or fp.bytes_done > fp.bytes_total:
                fp.bytes_total = fp.bytes_done
            fp.status = STATUS_DONE
            _logger.info("download done: %s (%s)", fp.name, format_bytes(fp.bytes_done))
        except asyncio.CancelledError:
            fp.status = STATUS_CANCELLED
            raise
        except aiohttp.ClientError as exc:
            fp.status = STATUS_FAILED
            fp.error = str(exc) or exc.__class__.__name__
            _logger.warning("download failed: %s error=%s", fp.name, fp.error)
        except Exception as exc:  # noqa: BLE001 - report any transport error
            fp.status = STATUS_FAILED
            fp.error = str(exc) or exc.__class__.__name__
            _logger.warning("download failed: %s error=%s", fp.name, fp.error)
        finally:
            await emit(force=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _await_unpause(is_paused: Callable[[], bool] | None, cancelled: Callable[[], bool]) -> None:
    if is_paused is None:
        return
    while True:
        try:
            paused = bool(is_paused())
        except Exception:
            paused = False
        if not paused or cancelled():
            return
        await asyncio.sleep(0.2)


def _content_total(response, resume_from: int) -> int | None:
    # Content-Range wins on a 206 (it carries the full size); otherwise add the
    # already-downloaded bytes to Content-Length.
    content_range = response.headers.get("Content-Range")
    if content_range and "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            return int(tail)
    length = response.headers.get("Content-Length")
    if length and str(length).isdigit():
        return int(length) + max(0, resume_from)
    return None


def _existing_complete_path(item: DownloadItem) -> Path | None:
    dest = Path(item.dest)
    if not dest.exists() or not dest.is_file():
        return None
    size = _safe_size(dest)
    if size <= 0:
        return None
    if item.expected_size and abs(size - item.expected_size) > max(1024, int(item.expected_size * 0.02)):
        return None
    return dest


def _safe_size(path: str | Path) -> int:
    try:
        return int(Path(path).stat().st_size)
    except OSError:
        return 0


_SSL_CONTEXT = None


def _ssl_context():
    """Build an SSL context that trusts certifi's CA bundle.

    Frozen builds frequently ship without a usable system trust store; this
    mirrors the TLS handling the legacy urllib fallback relied on so direct
    HTTPS downloads do not fail with certificate errors.
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT
    try:
        import ssl

        import certifi

        _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            import ssl

            _SSL_CONTEXT = ssl.create_default_context()
        except Exception:
            _SSL_CONTEXT = False  # sentinel: let aiohttp use its default
    return _SSL_CONTEXT if _SSL_CONTEXT is not False else None


def items_from_urls(
    urls: Iterable[str],
    dest_dir: str | Path,
    *,
    expected_sizes: Sequence[int | None] | None = None,
) -> list[DownloadItem]:
    """Convenience builder used by the direct-URL sources (JSOC/Helioviewer)."""
    dest_dir = Path(dest_dir).expanduser()
    out: list[DownloadItem] = []
    url_list = list(urls)
    for idx, url in enumerate(url_list):
        name = Path(str(url).split("?", 1)[0]).name or f"download_{idx}.fits"
        size = None
        if expected_sizes is not None and idx < len(expected_sizes):
            size = expected_sizes[idx]
        out.append(DownloadItem(url=str(url), dest=dest_dir / name, expected_size=size))
    return out
