"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The event story of a project report (src/backend/session/event_story.py).

From the loaded radio data, the event catalogs (GOES flares, CDAW and DONKI
CMEs, DONKI shock arrivals and storms, Monstein's burst list), a GCS
reconstruction and the geomagnetic response, this tells what happened, in
order, from the Sun to the Earth:

* a headline and a one-line chain ("X3.9 flare → type II burst → halo CME → …"),
* four chapters — at the Sun, in the corona, through the heliosphere, at Earth,
* key numbers, a timeline of milestones, the sources used and the caveats,
* and the *event chain* graphic: one swim lane per chapter on a compressed time
  axis (minutes near the eruption, days at Earth), its key steps joined by the
  time between them.

The pieces are associated by timing, and by DONKI's own links where it gives
them, and the story says so. It also says when they do *not* fit together: a
storm that peaked before this event's shock arrived was driven by earlier
eruptions, however large it was. Everything is optional; a radio file with no
catalogs still gets a short story. Qt-free.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

AU_KM = 1.495978707e8

PHASES = ("sun", "corona", "heliosphere", "earth")
PHASE_TITLES = {
    "sun": "At the Sun",
    "corona": "In the corona",
    "heliosphere": "Through the heliosphere",
    "earth": "At Earth",
}
PHASE_LANES = {"sun": "Sun", "corona": "Corona", "heliosphere": "Heliosphere", "earth": "Earth"}
#: Okabe–Ito colours, one per chapter.
PHASE_COLORS = {"sun": "#E69F00", "corona": "#CC79A7", "heliosphere": "#0072B2", "earth": "#009E73"}

#: How far from the event start the catalogs are searched for its pieces.
FLARE_BEFORE = timedelta(hours=3)
FLARE_AFTER = timedelta(minutes=30)
CME_BEFORE = timedelta(minutes=45)
CME_AFTER = timedelta(hours=3)
#: Sun–Earth transit times a CME shock can plausibly have (the fastest known ≈ 15 h).
TRANSIT_MIN = timedelta(hours=12)
TRANSIT_MAX = timedelta(days=5)
#: Burst-list entries longer than this (all-day continuum, say) are background, not the event.
BURST_MAX_DURATION = timedelta(hours=6)
#: The geomagnetic window the story looks at, around the event start.
EARTH_BEFORE = timedelta(days=1)
EARTH_AFTER = timedelta(days=7)

_CLASS_SCALE = {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}
_MINUS = "−"


# --------------------------------------------------------------------- inputs


@dataclass
class RadioSummary:
    """What the loaded radio data and its analysis say about the event."""

    start: datetime
    end: datetime
    station: str = ""
    label: str = ""
    #: Shock numbers from the Analyzer (plasma-density model inversion).
    shock_speed_km_s: float | None = None
    initial_shock_speed_km_s: float | None = None
    shock_height_rs: float | None = None
    drift_mhz_s: float | None = None
    #: Type II band-splitting results, when analysed.
    alfven_mach: float | None = None
    magnetic_field_g: float | None = None


@dataclass
class GCSKinematics:
    """A GCS (graduated cylindrical shell) reconstruction of the CME."""

    times: tuple[datetime, ...]
    heights_rsun: tuple[float, ...]
    lon_deg: float
    lat_deg: float
    tilt_deg: float = math.nan
    speed_km_s: float | None = None

    @property
    def earth_angle_deg(self) -> float:
        """Angle between the propagation direction and the Sun–Earth line."""
        lon, lat = math.radians(self.lon_deg), math.radians(self.lat_deg)
        return math.degrees(math.acos(max(-1.0, min(1.0, math.cos(lat) * math.cos(lon)))))


# -------------------------------------------------------------------- outputs


@dataclass(frozen=True)
class Milestone:
    time: datetime
    phase: str
    title: str
    detail: str = ""
    source: str = ""
    kind: str = ""
    #: A step of the cause-and-effect chain, drawn and joined on the chain graphic.
    key: bool = False
    #: Where the milestone lasts (a flare, an observation, a burst): drawn as a bar.
    end: datetime | None = None


@dataclass
class EventStory:
    anchor: datetime
    headline: str
    chain: str
    #: ``(phase, title, text)`` for each chapter that has something to say.
    chapters: list[tuple[str, str, str]] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)
    key_numbers: list[tuple[str, str]] = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def has_space_weather(self) -> bool:
        """Anything beyond the radio observation itself."""
        return any(m.kind not in ("radio", "burst") for m in self.milestones)


# ------------------------------------------------------------------- formatting


def goes_class_flux(goes_class: Any) -> float | None:
    """Peak 1–8 Å flux (W/m²) of a GOES class such as ``"X3.9"``."""
    match = re.match(r"\s*([ABCMX])\s*(\d+(?:\.\d+)?)?", str(goes_class or "").upper())
    if not match:
        return None
    return _CLASS_SCALE[match.group(1)] * float(match.group(2) or 1.0)


def signed(value: float, spec: str) -> str:
    """``format`` with a true minus sign."""
    return format(value, spec).replace("-", _MINUS)


def ut_text(when: datetime, anchor: datetime) -> str:
    """``06:27 UT`` on the event's day, ``11 May 02:00 UT`` on another."""
    if when.date() == anchor.date():
        return f"{when:%H:%M} UT"
    return f"{when.day} {when:%b %H:%M} UT"


def duration_text(delta: timedelta) -> str:
    minutes = abs(delta.total_seconds()) / 60.0
    if minutes < 90:
        return f"{minutes:.0f} min"
    hours = minutes / 60.0
    if hours < 36:
        return f"{hours:.1f} h"
    days = int(hours // 24)
    return f"{days} d {hours - 24 * days:.0f} h"


def elapsed_text(delta: timedelta) -> str:
    """``T+34.1 h`` relative to the event start."""
    sign = "+" if delta.total_seconds() >= 0 else _MINUS
    return f"T{sign}{duration_text(delta)}"


def _speed(value: Any) -> str:
    return f"{float(value):,.0f} km/s"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _flare_class(event: Any) -> str:
    return str((event.details or {}).get("class") or event.label or "").strip()


def _flare_region(event: Any) -> str:
    region = (event.details or {}).get("noaa_ar")
    return "" if region in (None, "", 0, "0") else str(region).strip()


def _is_type_ii(kind: str) -> bool:
    return str(kind or "").split("/")[0].strip().upper().rstrip("+-?") == "II"


def _type_list(types: Sequence[str]) -> str:
    if not types:
        return ""
    if len(types) == 1:
        return f"type {types[0]}"
    return "types " + ", ".join(types[:-1]) + f" and {types[-1]}"


# ------------------------------------------------------------------- selection


def _events(catalog: Any, source: str, kind: str | None = None) -> list[Any]:
    if catalog is None:
        return []
    events = list((getattr(catalog, "events", {}) or {}).get(source, []) or [])
    return [e for e in events if kind is None or e.kind == kind]


def _primary_flare(flares: Sequence[Any], anchor: datetime, radio_end: datetime) -> Any | None:
    """The largest flare around the radio event, preferring one under way during it."""
    window = [f for f in flares if anchor - FLARE_BEFORE <= f.start <= radio_end + FLARE_AFTER]
    if not window:
        return None
    during = [f for f in window if f.start <= radio_end and (f.end or f.peak or f.start) >= anchor - timedelta(minutes=15)]
    pool = during or window
    return max(pool, key=lambda f: (goes_class_flux(_flare_class(f)) or 0.0, -abs((f.start - anchor).total_seconds())))


def _fastest(events: Sequence[Any]) -> Any | None:
    if not events:
        return None
    return max(events, key=lambda e: (_number((e.details or {}).get("speed_kms")) or 0.0, -e.start.timestamp()))


def _marker_list(markers: Iterable[Mapping[str, Any]], catalog: Any) -> list[dict[str, Any]]:
    """DONKI shocks at Earth, storms and linked CMEs, from the geomagnetic markers and the catalog."""
    out: list[dict[str, Any]] = [dict(m) for m in markers or () if m.get("time") is not None]
    seen = {(m.get("kind"), m.get("time")) for m in out}
    for event in _events(catalog, "donki"):
        kind = None
        if event.kind == "ips" and str((event.details or {}).get("location") or "").lower() == "earth":
            kind = "ips"
        elif event.kind == "gst":
            kind = "gst"
        if kind and (kind, event.start) not in seen:
            out.append({"time": event.start, "label": event.label, "kind": kind, "url": event.url})
            seen.add((kind, event.start))
    return sorted(out, key=lambda m: m["time"])


def _of_kind(markers: Sequence[Mapping[str, Any]], kind: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    return [dict(m) for m in markers if m.get("kind") == kind and start <= m["time"] <= end]


@dataclass(frozen=True)
class _DonkiCME:
    time: datetime
    speed: float | None
    half_angle: float | None
    location: str
    linked: bool
    url: str


def _donki_cme(catalog: Any, markers: Sequence[Mapping[str, Any]], start: datetime, end: datetime) -> _DonkiCME | None:
    """The DONKI CME of the event: one DONKI links to the Earth impact first, else the fastest.

    The geomagnetic markers carry the linked CMEs too, so a story without the
    catalog's DONKI lane still names the CME that DONKI ties to the impact.
    """
    linked_times = [m["time"] for m in markers if m.get("kind") == "cme"]
    candidates = [e for e in _events(catalog, "donki", "cme") if start <= e.start <= end]
    linked = [e for e in candidates if any(abs((e.start - t).total_seconds()) <= 60 for t in linked_times)]
    event = _fastest(linked) or _fastest(candidates)
    if event is not None:
        details = event.details or {}
        return _DonkiCME(event.start, _number(details.get("speed_kms")), _number(details.get("half_angle_deg")),
                         str(details.get("source_location") or ""), event in linked, event.url or "")
    marker = next((m for m in markers if m.get("kind") == "cme" and start <= m["time"] <= end), None)
    if marker is None:
        return None
    speed = re.search(r"(\d+)\s*km/s", str(marker.get("label") or ""))
    return _DonkiCME(marker["time"], float(speed.group(1)) if speed else None, None, "", True, "")


# --------------------------------------------------------------------- the story


class _Story:
    """Collects the chapters, milestones and numbers as each part is told."""

    def __init__(self, anchor: datetime):
        self.anchor = anchor
        self.milestones: list[Milestone] = []
        self.numbers: list[tuple[str, str]] = []
        self.links: list[tuple[str, str]] = []
        self.chain: list[str] = []
        self.text: dict[str, list[str]] = {phase: [] for phase in PHASES}

    def ut(self, when: datetime) -> str:
        return ut_text(when, self.anchor)

    def mark(self, when: datetime, phase: str, title: str, detail: str = "", source: str = "", kind: str = "",
             key: bool = False, end: datetime | None = None) -> None:
        if end is not None and end <= when:
            end = None
        self.milestones.append(Milestone(when, phase, title, detail, source, kind, key, end))

    def say(self, phase: str, sentence: str) -> None:
        self.text[phase].append(sentence)


def build_event_story(
    *,
    radio: RadioSummary | None = None,
    catalog: Any = None,
    geomagnetic: Any = None,
    markers: Sequence[Mapping[str, Any]] = (),
    gcs: GCSKinematics | None = None,
) -> EventStory | None:
    """The story of the event, or None when there is no event time at all.

    ``catalog`` is an :class:`~src.backend.space_weather.event_catalogs.EventContextResult`,
    ``geomagnetic`` a :class:`~src.backend.space_weather.geomagnetic_context.GeomagneticContext`
    and ``markers`` its DONKI markers ({"time", "label", "kind"}).
    """
    anchor = radio.start if radio is not None else getattr(geomagnetic, "event_start", None)
    if anchor is None:
        return None
    story = _Story(anchor)
    radio_end = radio.end if radio is not None else anchor
    summary = getattr(geomagnetic, "summary", None)
    all_markers = _marker_list(markers, catalog)

    flare, flare_source = _tell_flare(story, catalog, radio_end)
    types = _tell_radio(story, radio, catalog)
    ref = flare.start if flare is not None else anchor
    cdaw, donki, cme_time, best_speed = _tell_cme(story, catalog, all_markers, gcs, ref)
    if radio is not None and radio.shock_speed_km_s and best_speed:
        speed, what = best_speed
        ratio = radio.shock_speed_km_s / speed
        story.say("corona", f"The radio shock speed is {ratio:.2f} times the {what} CME speed"
                  + (", comparable, as for a shock driven by the CME." if 0.6 <= ratio <= 1.6
                     else "; the two measure different parts of the disturbance, so a large difference is not unusual."))
    launch = cme_time or ref
    arrival = _tell_transit(story, all_markers, summary, launch, best_speed, cme_time is not None)
    storm = _tell_earth(story, geomagnetic, summary, all_markers, arrival)

    caveats = [
        "The flare, CME, shock and storm are associated by timing"
        + (" and by DONKI's own links" if donki is not None and donki.linked else "")
        + "; they are likely, not certain, to be one event."
    ]
    if cdaw is not None:
        caveats.append("CDAW speeds are projected onto the plane of the sky and underestimate Earth-directed (halo) CMEs.")
    if arrival is not None:
        caveats.append("The transit speed is the mean over 1 AU from the CME's first appearance to the shock at Earth.")
    if radio is not None and radio.shock_speed_km_s:
        caveats.append("The radio shock speed depends on the coronal density model chosen in the Analyzer.")
    sources = []
    if radio is not None:
        sources.append(("Radio", "e-CALLISTO network" + ("; burst types from the Monstein burst list" if types else "")))
    if flare is not None:
        sources.append(("Flares", "GOES X-ray flares via the Heliophysics Event Knowledge base (HEK)"
                        if flare_source.startswith("GOES") else "NASA CCMC DONKI flare list"))
    if cdaw is not None or donki is not None or arrival is not None or storm:
        sources.append(("CMEs, shocks, storms", "SOHO/LASCO CME catalog (CDAW) and NASA CCMC DONKI"))
    if gcs is not None:
        sources.append(("3D CME", "GCS reconstruction made in this project"))
    if summary is not None:
        sources.append(("Geomagnetic", "Kyoto WDC Dst, GFZ Potsdam Kp, NASA OMNI and NOAA L1 solar wind"))

    return EventStory(
        anchor=anchor,
        headline=_headline(story, radio, flare, cdaw, donki, arrival, summary, launch),
        chain=" → ".join(story.chain),
        chapters=[(phase, PHASE_TITLES[phase], " ".join(story.text[phase])) for phase in PHASES if story.text[phase]],
        milestones=sorted(story.milestones, key=lambda m: m.time),
        key_numbers=story.numbers,
        sources=sources + story.links,
        caveats=caveats,
    )


def _tell_flare(story: _Story, catalog: Any, radio_end: datetime) -> tuple[Any | None, str]:
    anchor = story.anchor
    flare = _primary_flare(_events(catalog, "flares", "flare"), anchor, radio_end)
    source = "GOES (HEK)"
    if flare is None:
        flare = _primary_flare(_events(catalog, "donki", "flare"), anchor, radio_end)
        source = "DONKI"
    if flare is None:
        return None, source
    cls = _flare_class(flare) or "flare"
    region = _flare_region(flare)
    location = str((flare.details or {}).get("source_location") or "")
    where = (f" in NOAA AR {region}" if region else "") + (f" ({location})" if location else "")
    peak = flare.peak or flare.start
    story.mark(flare.start, "sun", f"{cls} flare", f"GOES 1–8 Å{where}", source, "flare", key=True, end=flare.end)
    if flare.peak is not None:
        story.mark(peak, "sun", f"{cls} peak", "", source, "peak")
    text = f"A GOES {cls} flare erupted{where}, starting at {story.ut(flare.start)}"
    if flare.peak is not None:
        text += f" and peaking at {story.ut(peak)} ({duration_text(peak - flare.start)} later)"
    if flare.end is not None:
        text += f"; it ended at {story.ut(flare.end)}"
    story.say("sun", text + ".")
    others = [f for f in _events(catalog, "flares", "flare")
              if f is not flare and abs((f.start - anchor).total_seconds()) <= FLARE_BEFORE.total_seconds()]
    if others:
        biggest = max(others, key=lambda f: goes_class_flux(_flare_class(f)) or 0.0)
        largest = _flare_class(biggest) or "unclassified"
        story.say("sun", f"One other flare ({largest}) occurred within 3 h of the event." if len(others) == 1 else
                  f"{len(others)} other flares occurred within 3 h of the event (the largest {largest}).")
    story.numbers.append(("Flare", f"GOES {cls}, peak {story.ut(peak)}" + (f", AR {region}" if region else "")))
    story.chain.append(f"{cls} flare")
    if flare.url:
        story.links.append(("Flare", flare.url))
    return flare, source


def _tell_radio(story: _Story, radio: RadioSummary | None, catalog: Any) -> list[str]:
    if radio is None:
        return []
    window_start, window_end = radio.start - timedelta(minutes=10), radio.end + timedelta(minutes=10)
    bursts = [e for e in _events(catalog, "bursts")
              if e.start <= window_end and (e.end or e.start) >= window_start
              and (e.end or e.start) - e.start <= BURST_MAX_DURATION]
    types: list[str] = []
    for event in bursts:
        kind = str((event.details or {}).get("type") or "").strip()
        if kind and kind not in ("---", "?") and kind not in types:
            types.append(kind)
    has_type_ii = any(_is_type_ii(t) for t in types)
    station = radio.station or "e-CALLISTO"
    story.mark(radio.start, "corona", f"{station} observation", radio.label, "e-CALLISTO", "radio",
               key=not has_type_ii, end=radio.end)
    for event in bursts:
        if window_start <= event.start <= window_end:
            kind = str((event.details or {}).get("type") or "")
            story.mark(event.start, "corona", f"Type {kind} burst", "", "Burst list (Monstein)", "burst",
                       key=_is_type_ii(kind), end=event.end)
    text = f"e-CALLISTO station {station} recorded the radio spectrum from {story.ut(radio.start)} to {story.ut(radio.end)}"
    if types:
        text += f"; the burst list reports {_type_list(types)} emission in that interval"
        if has_type_ii:
            text += ", the type II being the radio signature of a coronal shock"
    story.say("corona", text + ".")
    story.numbers.append(("Radio", f"{station}, {story.ut(radio.start)}–{story.ut(radio.end)}"
                                   + (f", {_type_list(types)}" if types else "")))
    if radio.shock_speed_km_s:
        text = "The analysed burst"
        if radio.drift_mhz_s:
            text += f" drift ({signed(radio.drift_mhz_s, '.3g')} MHz/s)"
        text += f" implies a coronal shock speed of {_speed(radio.shock_speed_km_s)}"
        if radio.initial_shock_speed_km_s:
            text += f" (initially {_speed(radio.initial_shock_speed_km_s)})"
        if radio.shock_height_rs:
            text += f" at about {radio.shock_height_rs:.2f} R☉"
        story.say("corona", text + ".")
        story.numbers.append(("Radio shock speed", _speed(radio.shock_speed_km_s)))
    if radio.alfven_mach:
        text = f"Band splitting gives an Alfvén Mach number of {radio.alfven_mach:.2f}"
        if radio.magnetic_field_g:
            text += f" and a coronal field of {radio.magnetic_field_g:.2g} G"
        story.say("corona", text + ".")
    if has_type_ii:
        story.chain.append("type II burst")
    elif types:
        story.chain.append(f"type {types[0]} burst")
    return types


def _tell_cme(story: _Story, catalog: Any, markers: Sequence[Mapping[str, Any]], gcs: GCSKinematics | None,
              ref: datetime) -> tuple[Any | None, _DonkiCME | None, datetime | None, tuple[float, str] | None]:
    """The CME from CDAW, DONKI and the GCS fit; returns its time and best speed (value, which)."""
    window_start, window_end = ref - CME_BEFORE, ref + CME_AFTER
    cdaw = _fastest([e for e in _events(catalog, "cmes", "cme") if window_start <= e.start <= window_end])
    donki = _donki_cme(catalog, markers, window_start, window_end)
    cdaw_speed = None
    halo = False
    if cdaw is not None:
        details = cdaw.details or {}
        cdaw_speed = _number(details.get("speed_kms"))
        halo = bool(details.get("halo"))
        width = str(details.get("width") or "")
        story.mark(cdaw.start, "corona", "CME in LASCO C2" + (f", {_speed(cdaw_speed)}" if cdaw_speed else ""),
                   "halo" if halo else (f"width {width}" if width else ""), "CDAW LASCO catalog", "cme", key=True)
        text = f"LASCO C2 first saw a {'halo ' if halo else ''}CME at {story.ut(cdaw.start)}"
        if cdaw_speed:
            text += f", moving at {_speed(cdaw_speed)} in the plane of the sky"
        if width and not halo:
            text += f", {width} wide"
        story.say("corona", text + ".")
        story.numbers.append(("CME (CDAW)", ", ".join(p for p in (
            _speed(cdaw_speed) if cdaw_speed else "speed n/a", "halo" if halo else width,
            f"in C2 at {story.ut(cdaw.start)}") if p)))
        if cdaw.url:
            story.links.append(("CME (CDAW)", cdaw.url))
    if donki is not None:
        text = f"DONKI's analysis gives {_speed(donki.speed) if donki.speed else 'no speed'}"
        if donki.half_angle:
            text += f" and a full angular width of {2 * donki.half_angle:.0f}°"
        if donki.location:
            text += f", from {donki.location}"
        if donki.linked:
            text += "; DONKI links this CME to the shock or storm at Earth"
        story.say("corona", text + ".")
        story.mark(donki.time, "corona", "CME (DONKI)" + (f", {_speed(donki.speed)}" if donki.speed else ""),
                   "linked to the Earth impact" if donki.linked else "", "NASA DONKI", "cme", key=cdaw is None)
        story.numbers.append(("CME (DONKI)", ", ".join(p for p in (
            _speed(donki.speed) if donki.speed else "speed n/a",
            f"{2 * donki.half_angle:.0f}° wide" if donki.half_angle else "",
            "linked to the Earth impact" if donki.linked else "") if p)))
        if donki.url:
            story.links.append(("CME (DONKI)", donki.url))
    if gcs is not None and gcs.times:
        angle = gcs.earth_angle_deg
        facing = "Earth-directed" if angle <= 30 else ("partly Earth-facing" if angle <= 60 else "not Earth-directed")
        text = (f"The GCS reconstruction places the CME at Stonyhurst longitude {signed(gcs.lon_deg, '.0f')}°, "
                f"latitude {signed(gcs.lat_deg, '.0f')}° — {angle:.0f}° from the Sun–Earth line, {facing}")
        if gcs.speed_km_s:
            text += f"; its apex moved out at {_speed(gcs.speed_km_s)}"
        text += (f", from {min(gcs.heights_rsun):.1f} to {max(gcs.heights_rsun):.1f} R☉ between "
                 f"{story.ut(gcs.times[0])} and {story.ut(gcs.times[-1])}")
        story.say("corona", text + ".")
        story.mark(gcs.times[0], "corona", "GCS fit", f"{facing}, {angle:.0f}° off the Sun–Earth line",
                   "GCS reconstruction", "gcs")
        story.numbers.append(("CME (GCS 3D)", (f"{_speed(gcs.speed_km_s)}, " if gcs.speed_km_s else "")
                              + f"lon {signed(gcs.lon_deg, '.0f')}°, lat {signed(gcs.lat_deg, '.0f')}° ({facing})"))
    if cdaw is not None or donki is not None:
        speed = cdaw_speed or (donki.speed if donki is not None else None)
        story.chain.append((f"{_speed(speed)} " if speed else "") + ("halo CME" if halo else "CME"))
    # The 3D speeds (GCS, then DONKI's cone model) beat CDAW's projected one.
    best = None
    if gcs is not None and gcs.speed_km_s:
        best = (gcs.speed_km_s, "GCS 3D")
    elif donki is not None and donki.speed:
        best = (donki.speed, "DONKI")
    elif cdaw_speed:
        best = (cdaw_speed, "plane-of-sky")
    cme_time = cdaw.start if cdaw is not None else (donki.time if donki is not None else None)
    return cdaw, donki, cme_time, best


def _tell_transit(story: _Story, markers: Sequence[Mapping[str, Any]], summary: Any, launch: datetime,
                  best_speed: tuple[float, str] | None, from_cme: bool) -> dict[str, Any] | None:
    arrivals = _of_kind(markers, "ips", launch + TRANSIT_MIN, launch + TRANSIT_MAX)
    arrival = arrivals[0] if arrivals else None
    earlier = _of_kind(markers, "ips", story.anchor - EARTH_BEFORE, launch + TRANSIT_MIN - timedelta(seconds=1))
    if earlier:
        last = earlier[-1]
        story.say("heliosphere", f"A shock that reached Earth at {story.ut(last['time'])}, only "
                                 f"{duration_text(last['time'] - launch)} after the launch, came too soon to be this "
                                 "event's and belongs to an earlier eruption.")
        story.mark(last["time"], "heliosphere", "Earlier shock at Earth", "from an earlier eruption", "NASA DONKI", "shock")
    if arrival is not None:
        transit = arrival["time"] - launch
        mean_speed = AU_KM / transit.total_seconds()
        story.mark(arrival["time"], "heliosphere", "Shock at Earth", f"{duration_text(transit)} after launch",
                   "NASA DONKI", "shock", key=True)
        text = (f"A shock reached Earth at {story.ut(arrival['time'])}, {duration_text(transit)} after "
                f"{'the CME left' if from_cme else 'the flare began'}: a mean transit speed of {_speed(mean_speed)} over 1 AU")
        if best_speed is not None:
            speed, what = best_speed
            change = mean_speed / speed
            if change < 0.85:
                text += f", slower than the {_speed(speed)} {what} launch speed, as the solar wind braked it"
            elif change > 1.15:
                text += f", faster than the {_speed(speed)} {what} speed"
                if what == "plane-of-sky":
                    text += " (projection hides much of a halo CME's true speed)"
            else:
                text += f", close to the {_speed(speed)} {what} launch speed"
        story.say("heliosphere", text + ".")
        story.numbers.append(("Sun–Earth transit", f"{duration_text(transit)} (mean {_speed(mean_speed)})"))
        story.chain.append(f"shock at Earth after {duration_text(transit)}")
        if arrival.get("url"):
            story.links.append(("Shock (DONKI)", str(arrival["url"])))
    if summary is not None:
        if summary.v_max_kms is not None and summary.v_max_time is not None:
            story.say("heliosphere", f"At L1 the solar wind peaked at {_speed(summary.v_max_kms)} ({story.ut(summary.v_max_time)}).")
            story.mark(summary.v_max_time, "heliosphere", f"V max {summary.v_max_kms:,.0f} km/s", "", "OMNI / L1", "wind")
            story.numbers.append(("Peak solar-wind speed", _speed(summary.v_max_kms)))
        if summary.bz_min_nt is not None and summary.bz_min_time is not None:
            south = summary.bz_min_nt < -10
            story.say("heliosphere",
                      f"The IMF turned {'strongly ' if summary.bz_min_nt < -20 else ''}southward, to Bz = "
                      f"{signed(summary.bz_min_nt, '.1f')} nT at {story.ut(summary.bz_min_time)}"
                      + (", which lets the solar wind's energy into the magnetosphere." if south else "."))
            story.mark(summary.bz_min_time, "heliosphere", f"Bz min {signed(summary.bz_min_nt, '.1f')} nT", "",
                       "OMNI / L1", "bz")
            story.numbers.append(("Most southward Bz", f"{signed(summary.bz_min_nt, '.1f')} nT"))
    return arrival


def _storm_kp(storm: Mapping[str, Any]) -> str:
    """`` (Kp 6.3)`` from a DONKI storm label such as "Storm Kp 6.3"."""
    match = re.search(r"Kp\s*[\d.]+", str(storm.get("label") or ""))
    return f" ({match.group(0)})" if match else ""


def _stormy(summary: Any) -> bool:
    return summary.storm_class in ("moderate", "intense", "super") or summary.g_scale not in ("", "below G1")


def _tell_earth(story: _Story, geomagnetic: Any, summary: Any, markers: Sequence[Mapping[str, Any]],
                arrival: dict[str, Any] | None) -> bool:
    """The storm, told against the shock: did it follow it, or was it already under way?"""
    if geomagnetic is None or summary is None:
        story.say("earth", "The geomagnetic response was not loaded, so the effect at Earth is not told here.")
        return False
    anchor = story.anchor
    storms = _of_kind(markers, "gst", anchor - EARTH_BEFORE, anchor + EARTH_AFTER)
    for storm in storms:
        story.mark(storm["time"], "earth", f"DONKI storm{_storm_kp(storm)}", "", "NASA DONKI", "storm")
    peak_time = summary.dst_min_time or summary.kp_max_time
    before_arrival = arrival is not None and peak_time is not None and peak_time < arrival["time"]
    peak_is_ours = peak_time is not None and not before_arrival
    parts = []
    if summary.kp_max is not None and summary.kp_max_time is not None:
        level = summary.g_scale or ""
        parts.append(f"Kp reached {summary.kp_max:.1f}" + (f" ({level})" if level else "") + f" at {story.ut(summary.kp_max_time)}")
        story.mark(summary.kp_max_time, "earth", f"Kp {summary.kp_max:.1f}" + (f" ({level})" if level else ""), "",
                   "GFZ Kp", "kp")
        story.numbers.append(("Maximum Kp", f"{summary.kp_max:.1f}" + (f" ({level})" if level else "")))
    if summary.dst_min_nt is not None and summary.dst_min_time is not None:
        klass = summary.storm_class or ""
        dst = signed(summary.dst_min_nt, ".0f")
        parts.append(f"Dst fell to {dst} nT" + (f" ({klass} storm)" if klass not in ("", "quiet") else "")
                     + f" at {story.ut(summary.dst_min_time)}")
        story.mark(summary.dst_min_time, "earth", f"Dst min {dst} nT", klass, "Kyoto WDC Dst", "dst",
                   key=peak_is_ours and _stormy(summary))
        story.numbers.append(("Minimum Dst", f"{dst} nT" + (f" ({klass})" if klass else "")))
    if not parts:
        return bool(storms)
    peaks = "; ".join(parts)
    level = summary.g_scale if summary.g_scale not in ("", "below G1") else ""
    if not _stormy(summary):
        story.say("earth", f"Earth's magnetosphere stayed largely quiet: {peaks}.")
        story.chain.append("no significant storm")
        return bool(storms)
    if before_arrival:
        ongoing = [s for s in storms if s["time"] <= arrival["time"]]
        text = "Earth was already in a storm driven by earlier eruptions"
        if ongoing:
            text += f" (a DONKI storm from {story.ut(ongoing[0]['time'])})"
        text += (f": {peaks}, {duration_text(arrival['time'] - peak_time)} before this event's shock arrived. "
                 "So this CME did not cause the storm's peak; its shock arrived during the storm.")
        later = [s for s in storms if s["time"] > arrival["time"]]
        if later:
            text += f" DONKI records a further storm from {story.ut(later[0]['time'])}{_storm_kp(later[0])}."
        story.say("earth", text)
        story.chain.append(f"during a {level + ' ' if level else ''}storm already under way")
        return True
    text = f"The magnetosphere answered with a {level + ' ' if level else ''}geomagnetic storm: {peaks}"
    if arrival is not None and peak_time is not None:
        text += f", {duration_text(peak_time - arrival['time'])} after the shock arrived"
    elif peak_time is not None:
        text += f", {elapsed_text(peak_time - anchor)} from the event start"
    story.say("earth", text + ".")
    story.chain.append(f"{level + ' ' if level else ''}storm" + (f", Dst {signed(summary.dst_min_nt, '.0f')} nT"
                                                               if summary.dst_min_nt is not None else ""))
    return True


def _headline(story: _Story, radio, flare, cdaw, donki, arrival, summary, launch) -> str:
    anchor = story.anchor
    when = f"{anchor.day} {anchor:%B %Y}"
    pieces = []
    if flare is not None:
        cls = _flare_class(flare)
        region = _flare_region(flare)
        pieces.append((f"a GOES {cls} flare" if cls else "a flare") + (f" from NOAA AR {region}" if region else "")
                      + f" (peak {story.ut(flare.peak or flare.start)})")
    if cdaw is not None or donki is not None:
        speed = _number((cdaw.details or {}).get("speed_kms")) if cdaw is not None else donki.speed
        halo = cdaw is not None and bool((cdaw.details or {}).get("halo"))
        pieces.append((f"a {_speed(speed)} " if speed else "a ") + ("halo CME" if halo else "CME"))
    station = radio.station if radio is not None and radio.station else "e-CALLISTO"
    if pieces:
        first = f"On {when}, " + " and ".join(pieces) + (" was" if len(pieces) == 1 else " were") + " observed"
        first += f" while {station} recorded the radio event." if radio is not None else "."
    elif radio is not None:
        first = f"On {when}, {station} recorded the radio event from {story.ut(radio.start)} to {story.ut(radio.end)}."
    else:
        first = f"On {when}, an event was chosen for the geomagnetic response."
    second = f" A shock reached Earth {duration_text(arrival['time'] - launch)} later" if arrival is not None else ""
    if summary is not None and summary.dst_min_nt is not None:
        dst = signed(summary.dst_min_nt, ".0f")
        level = summary.g_scale if summary.g_scale not in ("", "below G1") else ""
        kp = f"Kp {summary.kp_max:.1f}, " if summary.kp_max is not None else ""
        peak_time = summary.dst_min_time or summary.kp_max_time
        storm = f"{level + ' ' if level else ''}"
        if not _stormy(summary):
            joined, alone = f", and Earth stayed largely quiet (Dst {dst} nT)", f"Earth stayed largely quiet (Dst {dst} nT)"
        elif arrival is not None and peak_time is not None and peak_time < arrival["time"]:
            joined = f", while Earth was already in a {storm}storm from earlier eruptions ({kp}Dst {dst} nT)"
            alone = f"Earth was already in a {storm}storm from earlier eruptions ({kp}Dst {dst} nT)"
        else:
            joined = f", and a {storm}geomagnetic storm followed ({kp}Dst {dst} nT)"
            alone = f"A {storm}geomagnetic storm followed ({kp}Dst {dst} nT)"
        second = (second + joined + ".") if second else (" " + alone + ".")
    elif second:
        second += "."
    return first + second


# ------------------------------------------------------------- the chain figure


_KIND_MARKERS = {
    "flare": "*", "peak": "*", "radio": "s", "burst": "^", "cme": "D", "gcs": "d",
    "shock": ">", "wind": "o", "bz": "v", "storm": "X", "kp": "P", "dst": "v",
}
#: Elapsed-time ticks (minutes) and their labels for the compressed axis.
_TICKS = (
    (-180, f"{_MINUS}3 h"), (-60, f"{_MINUS}1 h"), (-15, f"{_MINUS}15 min"), (0, "0"), (15, "+15 min"),
    (60, "+1 h"), (180, "+3 h"), (360, "+6 h"), (720, "+12 h"), (1440, "+1 d"), (2880, "+2 d"),
    (4320, "+3 d"), (7200, "+5 d"), (10080, "+7 d"),
)
#: Minutes over which the axis turns from linear to logarithmic.
_SCALE_MIN = 20.0


def _x(when: datetime, anchor: datetime) -> float:
    return math.asinh((when - anchor).total_seconds() / 60.0 / _SCALE_MIN)


def event_chain_figure(story: EventStory, *, width_in: float = 7.5) -> Any:
    """The event chain: one lane per chapter on a compressed time axis.

    Minutes around the eruption and days at Earth share one axis (asinh of the
    elapsed time), so a flare, its radio burst and a storm three days later all
    stay legible. Lasting milestones are bars; the key steps are joined by
    arrows giving the time between. Labels take the first free level above or
    below their lane; a minor label with no room left is dropped, never piled up.
    """
    from matplotlib.figure import Figure

    height_in = 4.9
    fig = Figure(figsize=(width_in, height_in), dpi=100, layout="constrained")
    ax = fig.add_subplot(1, 1, 1)
    anchor = story.anchor
    lanes = {phase: len(PHASES) - 1 - index for index, phase in enumerate(PHASES)}
    milestones = [m for m in story.milestones if m.phase in lanes]
    xs = [_x(m.time, anchor) for m in milestones] + [_x(m.end, anchor) for m in milestones if m.end] + [0.0]
    lo, hi = min(xs), max(xs)
    pad = max(0.6, 0.06 * (hi - lo))
    x_min, x_max = lo - pad, hi + pad
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(-0.6, len(PHASES) - 0.4)

    for phase, y in lanes.items():
        color = PHASE_COLORS[phase]
        ax.axhspan(y - 0.46, y + 0.46, color=color, alpha=0.07, linewidth=0)
        ax.axhline(y, color=color, alpha=0.35, linewidth=1.0, zorder=1)
    ax.set_yticks([lanes[p] for p in PHASES], [PHASE_LANES[p] for p in PHASES])
    for label, phase in zip(ax.get_yticklabels(), PHASES):
        label.set_color(PHASE_COLORS[phase])
        label.set_fontweight("bold")
    ax.axvline(0.0, color="#d62728", linestyle="--", linewidth=1.0, zorder=1)
    ax.annotate("event start", xy=(0.0, len(PHASES) - 0.4), xytext=(3, -2), textcoords="offset points",
                fontsize=6.5, color="#d62728", va="top")

    ticks = [(_x(anchor + timedelta(minutes=m), anchor), text) for m, text in _TICKS]
    ticks = [(x, text) for x, text in ticks if x_min <= x <= x_max]
    ax.set_xticks([x for x, _text in ticks], [text for _x_, text in ticks], fontsize=7)
    ax.set_xlabel(f"Time from the event start ({anchor:%Y-%m-%d %H:%M} UT), compressed scale", fontsize=8)
    ax.tick_params(axis="y", length=0, labelsize=8)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="x", color="#000000", alpha=0.06, linewidth=0.6)

    for milestone in milestones:
        x, y = _x(milestone.time, anchor), lanes[milestone.phase]
        color = PHASE_COLORS[milestone.phase]
        if milestone.end is not None:
            ax.plot([x, _x(milestone.end, anchor)], [y, y], color=color, linewidth=6 if milestone.key else 4,
                    alpha=0.45, solid_capstyle="butt", zorder=3)
        ax.plot([x], [y], marker=_KIND_MARKERS.get(milestone.kind, "o"), markersize=9 if milestone.key else 5.5,
                color=color, markeredgecolor="white", markeredgewidth=0.6, linestyle="none", zorder=4)

    # Labels: key steps first, each at the first level of its lane with room.
    axes_width_pt = width_in * 72.0 * 0.86
    levels = (8.0, -8.0, 26.0, -26.0)
    taken: dict[tuple[str, float], list[tuple[float, float]]] = {}
    for milestone in sorted(milestones, key=lambda m: (not m.key, m.time)):
        x, y = _x(milestone.time, anchor), lanes[milestone.phase]
        size = 6.2 if milestone.key else 5.4
        lines = (milestone.title, ut_text(milestone.time, anchor))
        width = 0.55 * size * max(len(line) for line in lines) + 4.0
        centre = (x - x_min) / (x_max - x_min) * axes_width_pt
        span = (centre - width / 2, centre + width / 2)
        free = [level for level in levels
                if all(span[1] + 3 < a or span[0] - 3 > b for a, b in taken.get((milestone.phase, level), []))]
        if not free:
            if not milestone.key:
                continue
            free = [min(levels, key=lambda level: len(taken.get((milestone.phase, level), [])))]
        level = free[0]
        taken.setdefault((milestone.phase, level), []).append(span)
        ax.annotate(
            "\n".join(lines), xy=(x, y), xytext=(0, level), textcoords="offset points",
            ha="center", va="bottom" if level > 0 else "top", fontsize=size,
            fontweight="bold" if milestone.key else "normal", color="#1f2937" if milestone.key else "#4b5563",
            zorder=5,
        )

    keys = [m for m in milestones if m.key]
    for first, second in zip(keys, keys[1:]):
        x0, y0 = _x(first.time, anchor), lanes[first.phase]
        x1, y1 = _x(second.time, anchor), lanes[second.phase]
        if abs(x1 - x0) < 1e-6 and y0 == y1:
            continue
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops={"arrowstyle": "-|>", "color": "#6b7280", "linewidth": 0.9, "shrinkA": 7, "shrinkB": 7,
                                "connectionstyle": "arc3,rad=-0.18"}, zorder=3)
        ax.annotate(duration_text(second.time - first.time), xy=((x0 + x1) / 2, (y0 + y1) / 2),
                    xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontsize=6,
                    color="#374151", zorder=6,
                    bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "edgecolor": "none", "alpha": 0.85})
    return fig
