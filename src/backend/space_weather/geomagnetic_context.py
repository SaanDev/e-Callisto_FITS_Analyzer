"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The geomagnetic response to a radio event (src/backend/space_weather/geomagnetic_context.py).

A CME launched with a type II burst reaches Earth one to four days later. This
gathers what Earth's environment did around it — by default from one day
before the event to seven days after:

* Dst (Kyoto, hourly; final, provisional or real-time per month) and SYM-H,
* Kp (GFZ Potsdam, 3-hourly), coloured by NOAA G-scale,
* the IMF (Bt, Bz, By), plasma (V, Np, T, Pdyn), derived parameters (Ey, β,
  Mach) and the AE/AL/AU indices from OMNI, with L1 data where OMNI is not
  yet published (see ``solar_wind``),

and summarises it: the Dst minimum and how long after the event it came, the
Kp maximum and its G level, the lowest Bz, the fastest wind and the storm
class. Every series keeps the provenance of each segment so a figure or CSV
can say which part is final, provisional, real-time, OMNI or L1.

Qt-free; every loader takes ``cache_dir``, ``progress_cb`` and ``cancel_cb``.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import numpy as np

from src.backend.space_weather.solar_wind import (
    SERIES_UNITS,
    Segment,
    SolarWindCancelled,
    load_solar_wind,
)

DEFAULT_BEFORE = timedelta(days=1)
DEFAULT_AFTER = timedelta(days=7)

#: Series names, labels and units, in panel order.
SERIES_LABELS: dict[str, str] = {
    "dst": "Dst",
    "kp": "Kp",
    "symh": "SYM-H",
    "bt": "Bt",
    "bz": "Bz (GSM)",
    "by": "By (GSM)",
    "v": "V",
    "np": "Np",
    "t": "T",
    "pdyn": "Pdyn",
    "ey": "Ey",
    "beta": "β",
    "mach": "Mach (Alfvén)",
    "ae": "AE",
    "al": "AL",
    "au": "AU",
}
UNITS: dict[str, str] = {"dst": "nT", "kp": "", **SERIES_UNITS}

ARRAY_PREFIX = "geo_"


class GeomagneticCancelled(RuntimeError):
    """The caller cancelled the load."""


@dataclass
class GeoSeries:
    """One quantity on its own time axis (naive UTC, ``datetime64[s]``), NaN for gaps."""

    name: str
    times: np.ndarray
    values: np.ndarray
    segments: list[Segment] = field(default_factory=list)

    @property
    def label(self) -> str:
        return SERIES_LABELS.get(self.name, self.name)

    @property
    def units(self) -> str:
        return UNITS.get(self.name, "")

    def finite(self) -> bool:
        return bool(np.isfinite(np.asarray(self.values, dtype=float)).any())


@dataclass
class GeomagneticSummary:
    dst_min_nt: float | None = None
    dst_min_time: datetime | None = None
    #: Hours from the event start to the Dst minimum.
    dst_min_delay_h: float | None = None
    storm_class: str = ""
    kp_max: float | None = None
    kp_max_time: datetime | None = None
    g_scale: str = ""
    bz_min_nt: float | None = None
    bz_min_time: datetime | None = None
    v_max_kms: float | None = None
    v_max_time: datetime | None = None


@dataclass
class GeomagneticContext:
    event_start: datetime
    start: datetime
    end: datetime
    series: dict[str, GeoSeries] = field(default_factory=dict)
    #: Kp interval status ("def", "nowcast" …), one per Kp sample.
    kp_status: list[str] = field(default_factory=list)
    summary: GeomagneticSummary = field(default_factory=GeomagneticSummary)
    notes: list[str] = field(default_factory=list)
    #: Source -> error text, for sources that failed (the rest still load).
    errors: dict[str, str] = field(default_factory=dict)
    #: Where the event time came from ("FITS: …", "Burst list: …").
    event_label: str = ""
    #: Vertical markers: DONKI shock arrivals at Earth, storms and the CMEs
    #: they are linked to — ``{"time", "label", "color", "kind"}``.
    markers: list[dict[str, Any]] = field(default_factory=list)

    def has_data(self) -> bool:
        return any(series.finite() for series in self.series.values())


# --------------------------------------------------------------------- metrics


def storm_class(dst_min_nt: float | None) -> str:
    """Gonzalez et al. (1994) classes from the Dst minimum."""
    if dst_min_nt is None or not math.isfinite(dst_min_nt):
        return ""
    if dst_min_nt <= -250:
        return "super"
    if dst_min_nt <= -100:
        return "intense"
    if dst_min_nt <= -50:
        return "moderate"
    if dst_min_nt <= -30:
        return "weak"
    return "quiet"


def g_scale(kp: float | None) -> str:
    """NOAA geomagnetic storm level for a Kp value (G1 = Kp 5 … G5 = Kp 9)."""
    if kp is None or not math.isfinite(kp):
        return ""
    level = int(math.floor(float(kp) + 1e-6)) - 4
    if level < 1:
        return "below G1"
    return f"G{min(5, level)}"


def g_level(kp: float) -> int:
    """0 below G1, else 1..5."""
    if not math.isfinite(kp):
        return 0
    return max(0, min(5, int(math.floor(float(kp) + 1e-6)) - 4))


def _to_datetime(value: np.datetime64) -> datetime:
    return value.astype("datetime64[s]").astype(datetime)


def _extreme(series: GeoSeries | None, start: datetime, *, low: bool) -> tuple[float | None, datetime | None]:
    if series is None or series.times.size == 0:
        return None, None
    values = np.asarray(series.values, dtype=float)
    mask = np.isfinite(values) & (series.times >= np.datetime64(start, "s"))
    if not mask.any():
        return None, None
    idx = np.flatnonzero(mask)
    pick = idx[int(np.argmin(values[idx]) if low else np.argmax(values[idx]))]
    return float(values[pick]), _to_datetime(series.times[pick])


def summarise(context: GeomagneticContext) -> GeomagneticSummary:
    """The response after the event: extremes from the event start to the end."""
    event = context.event_start
    out = GeomagneticSummary()
    out.dst_min_nt, out.dst_min_time = _extreme(context.series.get("dst"), event, low=True)
    if out.dst_min_time is not None:
        out.dst_min_delay_h = (out.dst_min_time - event).total_seconds() / 3600.0
    out.storm_class = storm_class(out.dst_min_nt)
    out.kp_max, out.kp_max_time = _extreme(context.series.get("kp"), event, low=False)
    out.g_scale = g_scale(out.kp_max)
    out.bz_min_nt, out.bz_min_time = _extreme(context.series.get("bz"), event, low=True)
    out.v_max_kms, out.v_max_time = _extreme(context.series.get("v"), event, low=False)
    return out


# --------------------------------------------------------------------- markers


MARKER_COLORS = {"ips": "#1f77b4", "gst": "#8c564b", "cme": "#9467bd"}


def donki_markers(events: Any) -> list[dict[str, Any]]:
    """Shock arrivals at Earth (IPS), storms (GST) and the CMEs DONKI links them to."""
    from src.backend.space_weather.event_catalogs import KIND_CME, KIND_GST, KIND_IPS, link_donki_events

    events = list(events or [])
    by_id = link_donki_events(events)
    markers: dict[tuple[str, datetime], dict[str, Any]] = {}

    def add(kind: str, when: datetime, label: str) -> None:
        markers.setdefault((kind, when), {"time": when, "label": label, "color": MARKER_COLORS[kind], "kind": kind})

    for event in events:
        if event.kind == KIND_IPS and str(event.details.get("location") or "").lower() == "earth":
            add("ips", event.start, "Shock at Earth (DONKI)")
        elif event.kind == KIND_GST:
            add("gst", event.start, event.label or "Storm (DONKI)")
        else:
            continue
        for ident in event.details.get("linked") or []:
            linked = by_id.get(str(ident))
            if linked is not None and linked.kind == KIND_CME:
                add("cme", linked.start, f"Linked {linked.label}")
    return sorted(markers.values(), key=lambda marker: marker["time"])


# --------------------------------------------------------------------- loading


def _segments_from_labels(times: np.ndarray, labels: list[str], prefix: str) -> list[Segment]:
    segments: list[Segment] = []
    if times.size == 0:
        return segments
    start_index = 0
    for index in range(1, len(labels) + 1):
        if index == len(labels) or labels[index] != labels[start_index]:
            segments.append(
                Segment(_to_datetime(times[start_index]), _to_datetime(times[index - 1]), f"{prefix} ({labels[start_index]})")
            )
            start_index = index
    return segments


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _call_cancellable(fn: Callable[[], Any], cancel_cb: Callable[[], bool] | None, *, poll_s: float = 0.2) -> Any:
    """``fn()`` for a loader with no cancel hook of its own (one blocking request).

    It runs on a daemon thread and is waited for in short steps, so Stop
    returns at once; an abandoned request finishes in the background and its
    result is dropped.
    """
    if cancel_cb is None:
        return fn()
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # handed back to the caller below
            outcome["error"] = exc

    thread = threading.Thread(target=run, name="geomagnetic-load", daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(poll_s)
        try:
            stop = bool(cancel_cb())
        except Exception:
            stop = False
        if stop and thread.is_alive():
            raise GeomagneticCancelled("Geomagnetic load cancelled.")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def load_geomagnetic_context(
    event_start_utc: datetime,
    *,
    before: timedelta = DEFAULT_BEFORE,
    after: timedelta = DEFAULT_AFTER,
    cache_dir: Any,
    session: Any = None,
    progress_cb: Callable[[int | None, str | None], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    now: datetime | None = None,
    event_label: str = "",
    loaders: Mapping[str, Callable[..., Any]] | None = None,
) -> GeomagneticContext:
    """Dst, Kp and solar wind from ``event − before`` to ``event + after``.

    Each source is loaded on its own; one failing records its error and the
    rest still load. ``loaders`` replaces ``dst`` / ``kp`` / ``solar_wind``
    (tests).
    """
    from src.backend.space_weather.dst_index import DstCancelled, load_dst_samples
    from src.backend.space_weather.kp_index import load_kp_range

    loaders = dict(loaders or {})
    event = _naive_utc(event_start_utc)
    start = event - before
    end = event + after
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    fetch_end = min(end, now)
    context = GeomagneticContext(event_start=event, start=start, end=end, event_label=event_label)

    def cancelled() -> bool:
        try:
            return bool(cancel_cb and cancel_cb())
        except Exception:
            return False

    def report(value: int | None, text: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(value, text)
            except Exception:
                pass

    if fetch_end <= start:
        context.notes.append("The whole window is in the future; nothing has been observed yet.")
        return context
    if end > now:
        context.notes.append(f"Data end at the present ({now:%Y-%m-%d %H:%M} UTC); the window runs to {end:%Y-%m-%d}.")

    # Dst ---------------------------------------------------------------------
    report(5, "Kyoto Dst…")
    try:
        dst_loader = loaders.get("dst", load_dst_samples)
        times, values, sources = dst_loader(start, fetch_end, session=session, cancel_cb=cancel_cb)
        dst_times = np.array([np.datetime64(t, "s") for t in times], dtype="datetime64[s]")
        context.series["dst"] = GeoSeries(
            "dst", dst_times, np.asarray(values, dtype=float),
            _segments_from_labels(dst_times, list(sources), "Kyoto Dst"),
        )
    except (DstCancelled, GeomagneticCancelled):
        raise GeomagneticCancelled("Geomagnetic load cancelled.")
    except Exception as exc:
        context.errors["Dst"] = str(exc) or type(exc).__name__
    if cancelled():
        raise GeomagneticCancelled("Geomagnetic load cancelled.")

    # Kp ----------------------------------------------------------------------
    report(20, "GFZ Kp…")
    try:
        kp_loader = loaders.get("kp", load_kp_range)
        # GFZ answers in one request that cannot be interrupted; Stop does not wait for it.
        kp = _call_cancellable(lambda: kp_loader(start, fetch_end, session=session), cancel_cb)
        kp_times = np.array([np.datetime64(t, "s") for t in kp.interval_starts], dtype="datetime64[s]")
        context.series["kp"] = GeoSeries(
            "kp", kp_times, np.asarray(kp.kp_decimal, dtype=float),
            _segments_from_labels(kp_times, [str(s or "def") for s in kp.status], kp.source_label or "GFZ Kp"),
        )
        context.kp_status = [str(s) for s in kp.status]
    except GeomagneticCancelled:
        raise
    except Exception as exc:
        context.errors["Kp"] = str(exc) or type(exc).__name__
    if cancelled():
        raise GeomagneticCancelled("Geomagnetic load cancelled.")

    # Solar wind ----------------------------------------------------------------
    report(35, "Solar wind and indices…")
    try:
        wind_loader = loaders.get("solar_wind", load_solar_wind)
        wind = wind_loader(
            start, fetch_end, cache_dir=cache_dir, session=session,
            progress_cb=lambda v, t: report(35 + int(0.6 * (v or 0)), t or "Solar wind…"),
            cancel_cb=cancel_cb, now=now,
        )
        for name, values in wind.series.items():
            context.series[name] = GeoSeries(name, wind.times, np.asarray(values, dtype=float), list(wind.segments.get(name, [])))
        context.notes.extend(wind.notes)
    except SolarWindCancelled:
        raise GeomagneticCancelled("Geomagnetic load cancelled.")
    except Exception as exc:
        context.errors["Solar wind"] = str(exc) or type(exc).__name__

    # DONKI markers --------------------------------------------------------------
    report(96, "DONKI shock arrivals and storms…")
    try:
        donki_loader = loaders.get("donki")
        if donki_loader is None:
            from src.backend.space_weather.event_catalogs import SOURCE_DONKI, load_catalog

            def donki_loader(first, last, **kwargs):
                return load_catalog(SOURCE_DONKI, first, last, cache_dir=cache_dir, session=session,
                                    cancel_cb=cancel_cb, now=now)

        # Linked CMEs left the Sun up to four days before their shock arrived.
        donki_events = donki_loader(start - timedelta(days=4), fetch_end)
        context.markers = [m for m in donki_markers(donki_events) if start - timedelta(days=4) <= m["time"] <= end]
    except Exception as exc:
        context.errors["DONKI"] = str(exc) or type(exc).__name__

    for source, message in context.errors.items():
        context.notes.append(f"{source} unavailable: {message.splitlines()[0] if message else 'error'}")
    context.summary = summarise(context)
    report(100, "Geomagnetic response ready.")
    return context


# --------------------------------------------------------------------- project


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", ""))
    except ValueError:
        return None


def summary_to_meta(summary: GeomagneticSummary) -> dict[str, Any]:
    out = {}
    for key, value in summary.__dict__.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, float):
            out[key] = value if math.isfinite(value) else None
        else:
            out[key] = value
    return out


def summary_from_meta(raw: Any) -> GeomagneticSummary:
    out = GeomagneticSummary()
    if not isinstance(raw, Mapping):
        return out
    for key in out.__dict__:
        value = raw.get(key)
        if key.endswith("_time"):
            setattr(out, key, _parse(value))
        elif key in ("storm_class", "g_scale"):
            setattr(out, key, str(value or ""))
        elif value is not None:
            try:
                setattr(out, key, float(value))
            except (TypeError, ValueError):
                pass
    return out


def context_to_project(context: GeomagneticContext) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """``(meta, arrays)``; arrays carry the ``geo_`` prefix and stay small."""
    meta = {
        "event_start": _iso(context.event_start),
        "start": _iso(context.start),
        "end": _iso(context.end),
        "event_label": context.event_label,
        "kp_status": list(context.kp_status),
        "summary": summary_to_meta(context.summary),
        "notes": list(context.notes),
        "errors": dict(context.errors),
        "markers": [
            {**marker, "time": marker["time"].isoformat()} for marker in context.markers if marker.get("time")
        ],
        "series": {},
    }
    arrays: dict[str, np.ndarray] = {}
    for name, series in context.series.items():
        meta["series"][name] = {
            "segments": [
                {"start": _iso(seg.start), "end": _iso(seg.end), "source": seg.source, "note": seg.note}
                for seg in series.segments
            ]
        }
        arrays[f"{ARRAY_PREFIX}t_{name}"] = series.times.astype("datetime64[s]").astype(np.int64)
        arrays[f"{ARRAY_PREFIX}v_{name}"] = np.asarray(series.values, dtype=np.float32)
    return meta, arrays


def context_from_project(meta: Mapping[str, Any], arrays: Mapping[str, Any]) -> GeomagneticContext | None:
    event = _parse(meta.get("event_start"))
    start = _parse(meta.get("start"))
    end = _parse(meta.get("end"))
    if event is None or start is None or end is None:
        return None
    context = GeomagneticContext(
        event_start=event, start=start, end=end,
        event_label=str(meta.get("event_label") or ""),
        kp_status=[str(s) for s in meta.get("kp_status") or []],
        summary=summary_from_meta(meta.get("summary")),
        notes=[str(n) for n in meta.get("notes") or []],
        errors={str(k): str(v) for k, v in dict(meta.get("errors") or {}).items()},
    )
    for marker in meta.get("markers") or []:
        when = _parse(marker.get("time")) if isinstance(marker, Mapping) else None
        if when is not None:
            context.markers.append({**marker, "time": when})
    for name, info in dict(meta.get("series") or {}).items():
        t_key, v_key = f"{ARRAY_PREFIX}t_{name}", f"{ARRAY_PREFIX}v_{name}"
        if t_key not in arrays or v_key not in arrays:
            continue
        times = np.asarray(arrays[t_key], dtype=np.int64).astype("datetime64[s]")
        values = np.asarray(arrays[v_key], dtype=float)
        segments = []
        for row in (info or {}).get("segments") or []:
            seg_start, seg_end = _parse(row.get("start")), _parse(row.get("end"))
            if seg_start is not None and seg_end is not None:
                segments.append(Segment(seg_start, seg_end, str(row.get("source") or ""), str(row.get("note") or "")))
        context.series[name] = GeoSeries(name, times, values, segments)
    return context


def context_csv_rows(context: GeomagneticContext) -> list[list[str]]:
    """``time, series, value, units, provenance`` rows, long format, for CSV export."""
    rows = [["time_utc", "series", "value", "units", "provenance"]]
    for name, series in context.series.items():
        for when, value in zip(series.times, series.values):
            if not np.isfinite(value):
                continue
            stamp = np.datetime64(when, "s")
            provenance = ""
            for seg in series.segments:
                if np.datetime64(seg.start, "s") <= stamp <= np.datetime64(seg.end, "s"):
                    provenance = seg.source + (f" ({seg.note})" if seg.note else "")
                    break
            rows.append([str(stamp).replace("T", " "), series.label, f"{float(value):.6g}", series.units, provenance])
    return rows
