"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Synoptic magnetograms: the photospheric boundary condition for PFSS.

An EUV image carries no magnetic field information, so a potential-field
extrapolation (``src.backend.solar.pfss_model``) has to be given the radial
photospheric field from somewhere else. That somewhere is a *synoptic* map: a
full-Sun Carrington chart assembled from the central meridian over an entire
solar rotation, because no instrument can see more than half the Sun at once.

Two consequences follow, and they are the honest limits of every PFSS overlay
this application draws:

  * A synoptic map is not a snapshot. Longitudes far from the observation's
    central meridian were measured days to weeks earlier, and the far side is
    always at least ~14 days stale. The model describes the *global* field
    around an observation, never the instantaneous field.
  * Four sources disagree, on purpose. GONG is ground-based and near-real-time;
    HMI is the SDO-native match for AIA but lags roughly a Carrington rotation;
    ADAPT runs a flux-transport model that assimilates observations, so it
    estimates the unobserved far side rather than leaving it stale, at the cost
    of being a model rather than a measurement.

Four sources are supported: :data:`SOURCE_GONG`, :data:`SOURCE_HMI`,
:data:`SOURCE_ADAPT` and :data:`SOURCE_LOCAL` (a file the user supplies).

The normalisation in :func:`load_magnetogram` matters more than it looks.
``sunkit_magex.pfss.Input`` accepts only a full-Sun Carrington map in a
cylindrical equal-area projection with **no non-finite pixels** -- it raises
``ValueError`` on a single NaN -- and real GONG maps have NaN gaps over the
poles, where the map is most foreshortened. Patching them is therefore
mandatory rather than defensive, and the count is reported so a badly gapped map
is visible instead of silent.

Note the module deliberately does *not* subtract the mean field. A net radial
flux (magnetic monopole) is unphysical, but ``pfss()`` already excludes the
monopole term from its solution, so removing it here would be redundant and
would misrepresent the input. It is recorded as a quality diagnostic instead.

No Qt here so the module stays unit-testable.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #

SOURCE_GONG = "gong"
SOURCE_HMI = "hmi"
SOURCE_ADAPT = "adapt"
SOURCE_LOCAL = "local"

MAGNETOGRAM_SOURCES: tuple[str, ...] = (SOURCE_GONG, SOURCE_HMI, SOURCE_ADAPT, SOURCE_LOCAL)

SOURCE_LABELS: dict[str, str] = {
    SOURCE_GONG: "GONG synoptic",
    SOURCE_HMI: "HMI synoptic (JSOC)",
    SOURCE_ADAPT: "GONG ADAPT",
    SOURCE_LOCAL: "Local FITS file...",
}

SOURCE_DESCRIPTIONS: dict[str, str] = {
    SOURCE_GONG: (
        "Ground-based GONG synoptic map, updated daily and available for any\n"
        "date from 2006 on. No registration needed; the usual default."
    ),
    SOURCE_HMI: (
        "SDO's own synoptic map, so it matches AIA instrumentally.\n"
        "Browsing is free; downloading stages a JSOC export and needs a\n"
        "registered e-mail. Published about one Carrington rotation behind,\n"
        "so the most recent few weeks are not covered."
    ),
    SOURCE_ADAPT: (
        "GONG ADAPT flux-transport model: estimates the unobserved far side\n"
        "instead of leaving it weeks stale, which usually improves open-field\n"
        "predictions. One file holds 12 realizations; pick one below."
    ),
    SOURCE_LOCAL: (
        "A synoptic FITS file you already have. Must be a full-Sun Carrington\n"
        "map; plate-carree maps are reprojected to equal-area automatically."
    ),
}

#: JSOC series holding the pole-filled HMI synoptic radial field.
HMI_SYNOPTIC_SERIES = "hmi.synoptic_mr_polfil_720s"

#: ADAPT longitude type. "0" is the Carrington fixed frame, which is what PFSS
#: wants; the alternatives are central-meridian and east-limb referenced.
ADAPT_LON_TYPE_CARRINGTON = "0"

#: Shape PFSS input is resampled down to. A 3600x1440 HMI synoptic map carries
#: far more spatial detail than a potential-field extrapolation can use, and the
#: solve cost grows with it, so it is binned to the conventional 1 degree grid.
DEFAULT_PFSS_SHAPE = (360, 180)

#: Default half-width of the search window around an observation.
DEFAULT_SEARCH_WINDOW = timedelta(days=1)

SUNPY_INSTALL_HINT = "Install it with: python3 -m pip install 'sunpy[map,net]'"


class MagnetogramError(RuntimeError):
    """A magnetogram could not be found, downloaded or normalised."""


@dataclass(frozen=True)
class MagnetogramRow:
    """One candidate synoptic magnetogram from a search."""

    source: str
    label: str = ""
    url: str = ""
    path: str = ""
    obstime: Any = None
    carrington_rotation: int | None = None
    size_bytes: int | None = None
    realizations: int = 1
    #: Opaque handle kept so the fetch step can hand the exact row back to Fido
    #: rather than re-running the search and hoping for the same ordering.
    query_result: Any = None

    @property
    def is_local(self) -> bool:
        return self.source == SOURCE_LOCAL or (not self.url and bool(self.path))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _import_net() -> tuple[Any, Any]:
    """Return ``(Fido, attrs)``, or raise with an actionable message."""
    try:
        from sunpy.net import Fido
        from sunpy.net import attrs as a
    except Exception as exc:  # pragma: no cover - sunpy is a hard dependency
        raise MagnetogramError(
            f"Searching for magnetograms needs sunpy's network stack ({exc}).\n{SUNPY_INSTALL_HINT}"
        ) from exc
    return Fido, a


def carrington_rotation(when: Any) -> int | None:
    """Carrington rotation number containing ``when``."""
    try:
        from astropy.time import Time
        from sunpy.coordinates.sun import carrington_rotation_number

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return int(np.floor(float(carrington_rotation_number(Time(when)))))
    except Exception:
        return None


def _as_datetime(value: Any) -> datetime | None:
    """Best-effort datetime from a Time, datetime or string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    for attr in ("datetime", "to_datetime"):
        candidate = getattr(value, attr, None)
        if candidate is None:
            continue
        try:
            return candidate() if callable(candidate) else candidate
        except Exception:
            continue
    try:
        from astropy.time import Time

        return Time(value).datetime
    except Exception:
        return None


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(str(source), str(source))


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

def _search_adapt(start: datetime, end: datetime, attrs: Any) -> Any:
    """ADAPT search, working around a stale filename pattern in sunpy.

    ``sunpy.net.dataretriever.ADAPTClient`` carries two filename patterns and
    picks between them by date, switching on 2024-09-28. The pre-switch pattern
    expects a *letter* for the version month (``adapt40311_04a012_...``), but the
    NSO archive has since been re-versioned so every file from 2012 onward uses a
    *digit* there (``adapt40311_044012_...``). The old pattern therefore matches
    nothing, and a plain ``Fido.search`` for any date before 2024-09-28 returns
    zero rows without raising -- which looks exactly like "no data for that date".

    Verified against the live archive (2026-09): listings for 2012, 2017, 2020,
    2022, 2023, 2024, 2025 and 2026 all use the digit form, and forcing the
    post-2024 pattern returns rows for every one of them.

    Falls back to the ordinary client path if this internal keyword ever goes
    away, so a fixed sunpy keeps working rather than breaking here.
    """
    from sunpy.net.dataretriever import ADAPTClient

    client = ADAPTClient()
    query = (attrs.Time(start, end), attrs.Instrument("adapt"))
    try:
        from sunpy.net.dataretriever.client import GenericClient

        return GenericClient.search(client, *query, adapt_use_new_pattern=True)
    except Exception:
        return client.search(*query)


def search_magnetograms(
    source: str,
    when: Any,
    *,
    window: timedelta = DEFAULT_SEARCH_WINDOW,
    email: str = "",
    fido_client: Any | None = None,
) -> list[MagnetogramRow]:
    """Find candidate synoptic magnetograms near ``when``.

    ``fido_client`` is injectable so tests (and the UI's own fakes) can run
    without network access, matching the seam
    ``src.backend.solar.sunpy_archive.fetch`` already uses.
    """
    source = str(source or SOURCE_GONG)
    if source == SOURCE_LOCAL:
        raise MagnetogramError("A local magnetogram is chosen with the file browser, not searched.")

    Fido, a = _import_net()
    client = fido_client or Fido
    moment = _as_datetime(when) or datetime.utcnow()
    start, end = moment - window, moment + window

    try:
        if source == SOURCE_GONG:
            query = (a.Time(start, end), a.Instrument("gong"), a.ExtentType("synoptic"))
        elif source == SOURCE_ADAPT:
            if fido_client is None:
                # Own search path; see _search_adapt for why Fido cannot be used
                # directly for dates before 2024-09-28.
                return _rows_from_result(_search_adapt(start, end, a), source, moment)
            query = (
                a.Time(start, end),
                a.Instrument("adapt"),
                a.adapt.ADAPTLonType(ADAPT_LON_TYPE_CARRINGTON),
            )
        elif source == SOURCE_HMI:
            rotation = carrington_rotation(moment)
            if rotation is None:
                raise MagnetogramError("Could not work out the Carrington rotation for that time.")
            # The email is deliberately NOT required here. A JSOC search is an
            # anonymous metadata query -- verified against the live archive --
            # so availability can be browsed without configuring JSOC at all.
            # Only the export staged in fetch_magnetogram needs a registered
            # address.
            parts = [
                a.jsoc.Series(HMI_SYNOPTIC_SERIES),
                a.jsoc.PrimeKey("CAR_ROT", int(rotation)),
            ]
            if str(email).strip():
                parts.append(a.jsoc.Notify(str(email).strip()))
            query = tuple(parts)
        else:
            raise MagnetogramError(f"Unknown magnetogram source '{source}'.")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = client.search(*query)
    except MagnetogramError:
        raise
    except Exception as exc:
        raise MagnetogramError(f"Magnetogram search failed: {type(exc).__name__}: {exc}") from exc

    return _rows_from_result(result, source, moment)


def _rows_from_result(result: Any, source: str, moment: datetime) -> list[MagnetogramRow]:
    """Flatten a Fido result into rows, nearest the requested time first."""
    rows: list[MagnetogramRow] = []
    # Fido returns a UnifiedResponse (iterable of tables); a client's own search
    # returns one table, where iterating would walk rows instead.
    if hasattr(result, "colnames"):
        tables = [result]
    else:
        try:
            tables = list(result)
        except Exception:
            tables = []

    for table in tables:
        for index in range(len(table)):
            record = table[index]
            obstime = _record_value(record, ("Start Time", "T_REC", "T_OBS", "Time"))
            rotation = _record_value(record, ("CAR_ROT", "Carrington Rotation"))
            url = _record_value(record, ("url", "URL", "fileid"))
            resolved_rotation = _safe_int(rotation)
            if resolved_rotation is None:
                resolved_rotation = carrington_rotation_from_name(Path(str(url or "")).name)
            if resolved_rotation is None and obstime is not None:
                # ADAPT names carry no rotation code, so derive it from the
                # timestamp: a synoptic map is identified by its rotation first.
                resolved_rotation = carrington_rotation(obstime)
            rows.append(
                MagnetogramRow(
                    source=source,
                    label=_row_label(source, obstime, resolved_rotation),
                    url=str(url or ""),
                    obstime=obstime,
                    carrington_rotation=resolved_rotation,
                    realizations=12 if source == SOURCE_ADAPT else 1,
                    query_result=table[index : index + 1],
                )
            )

    def distance(row: MagnetogramRow) -> float:
        stamp = _as_datetime(row.obstime)
        if stamp is None:
            return float("inf")
        return abs((stamp - moment).total_seconds())

    rows.sort(key=distance)
    return rows


def _record_value(record: Any, keys: Iterable[str]) -> Any:
    for key in keys:
        try:
            value = record[key]
        except Exception:
            continue
        if value is not None:
            return value
    return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def carrington_rotation_from_name(name: str) -> int | None:
    """Carrington rotation parsed out of a GONG/ADAPT filename.

    GONG synoptic results come back without a ``CAR_ROT`` column, but the file is
    named e.g. ``mrzqs230615t1204c2272_281.fits.gz``, where ``c2272`` is the
    rotation and ``_281`` the central-meridian longitude. Reading it here means
    the rotation shows in the chooser, not only after the file is downloaded.
    """
    match = re.search(r"[cC](\d{4})[_.]", str(name or ""))
    if match is None:
        return None
    rotation = _safe_int(match.group(1))
    # Carrington numbering passed 1600 in 1975 and will not reach 3000 this
    # century; anything outside that is a coincidental digit run.
    if rotation is None or not (1600 <= rotation <= 3000):
        return None
    return rotation


def _row_label(source: str, obstime: Any, rotation: Any) -> str:
    """Chooser label for one candidate.

    A synoptic map is identified by its Carrington rotation first and its
    timestamp second, and for ``hmi.synoptic_mr_polfil_720s`` JSOC reports the
    time columns as "Invalid KeyLink" anyway -- so the rotation leads when there
    is no usable timestamp, rather than showing "unknown time".
    """
    stamp = _as_datetime(obstime)
    rot = _safe_int(rotation)
    name = source_label(source)
    if stamp is None:
        return f"{name}: CR {rot}" if rot else name
    when = stamp.strftime("%Y-%m-%d %H:%M")
    return f"{name}: {when} - CR {rot}" if rot else f"{name}: {when}"


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #

def magnetogram_cache_dir(cache_dir: str | Path) -> Path:
    """Where downloaded synoptic maps live.

    A subfolder of the window's own cache root, so the existing "clear cache"
    action reaches it and nothing here is treated as irreplaceable.
    """
    out = Path(cache_dir).expanduser() / "pfss_magnetograms"
    out.mkdir(parents=True, exist_ok=True)
    return out


def fetch_magnetogram(
    row: MagnetogramRow,
    cache_dir: str | Path,
    *,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    email: str = "",
    fido_client: Any | None = None,
) -> Path:
    """Download ``row`` into the cache, or return the cached file.

    A synoptic map is a single file of a few megabytes, so this deliberately uses
    ``Fido.fetch`` directly rather than the adaptive batching machinery in
    ``sunpy_archive``, which exists for multi-hundred-file image series. Files
    already present are returned untouched, which is what makes re-solving and
    switching parameters cheap.
    """
    if row.is_local and row.path:
        path = Path(row.path).expanduser()
        if not path.is_file():
            raise MagnetogramError(f"Magnetogram file not found: {path}")
        return path

    target_dir = magnetogram_cache_dir(cache_dir)
    cached = _cached_file(target_dir, row)
    if cached is not None:
        if progress_cb is not None:
            progress_cb(1, 1)
        return cached

    if cancel_cb is not None and cancel_cb():
        raise InterruptedError("Magnetogram download was cancelled.")

    if row.source == SOURCE_HMI and not str(email).strip():
        # Searching JSOC is anonymous; staging an export is not.
        raise MagnetogramError(
            "Downloading an HMI synoptic map stages an export on JSOC, which "
            "needs a registered address.\n"
            "Register at http://jsoc.stanford.edu/ajax/register_email.html, "
            "then set it in the Data Source section."
        )

    Fido, _attrs = _import_net()
    client = fido_client or Fido
    query = row.query_result
    if query is None:
        raise MagnetogramError("This magnetogram row cannot be downloaded (no query handle).")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            downloaded = client.fetch(
                query, path=str(target_dir / "{file}"), progress=False, overwrite=False
            )
    except Exception as exc:
        raise MagnetogramError(f"Magnetogram download failed: {type(exc).__name__}: {exc}") from exc

    errors = list(getattr(downloaded, "errors", []) or [])
    paths = [Path(str(p)) for p in list(downloaded)]
    if not paths:
        detail = f" ({errors[0]})" if errors else ""
        raise MagnetogramError(f"The magnetogram download returned no files{detail}.")
    if progress_cb is not None:
        progress_cb(1, 1)
    return paths[0]


def _cached_file(target_dir: Path, row: MagnetogramRow) -> Path | None:
    """An already-downloaded file matching ``row``, if there is one."""
    name = Path(str(row.url or "")).name.split("?")[0].strip()
    if not name:
        return None
    candidate = target_dir / name
    if candidate.is_file() and candidate.stat().st_size > 0:
        return candidate
    return None


# --------------------------------------------------------------------------- #
# Loading and normalisation
# --------------------------------------------------------------------------- #

def load_magnetogram(
    path: str | Path,
    *,
    source: str = SOURCE_LOCAL,
    realization: int = 0,
    frame_time: Any = None,
    target_shape: tuple[int, int] | None = DEFAULT_PFSS_SHAPE,
) -> tuple[Any, dict[str, Any]]:
    """Load a synoptic magnetogram and normalise it into PFSS input form.

    Returns ``(map, provenance)``. The map satisfies every precondition of
    ``sunkit_magex.pfss.Input``; the provenance dict is what the UI shows so the
    boundary condition is auditable rather than implicit.

    The steps, in the order they have to happen:

    1. **Read.** ADAPT packs 12 flux-transport realizations into one 3-D HDU,
       which ``sunpy.map.Map`` cannot read directly, so those are split first.
    2. **Repair HMI metadata.** HMI synoptic headers are not WCS-compliant and
       ``pfss.utils.fix_hmi_meta`` is the sanctioned patch.
    3. **Reproject if needed.** ADAPT is plate-carree; PFSS needs cylindrical
       equal area (equal steps in sin(latitude)). GONG and HMI already are.
    4. **Resample.** See :data:`DEFAULT_PFSS_SHAPE`.
    5. **Patch non-finite pixels.** Mandatory: ``Input`` raises on a single NaN,
       and GONG maps have polar gaps.
    """
    path = Path(path).expanduser()
    if not path.is_file():
        raise MagnetogramError(f"Magnetogram file not found: {path}")

    source = str(source or SOURCE_LOCAL)
    provenance: dict[str, Any] = {
        "source": source,
        "source_label": source_label(source),
        "path": str(path),
        "filename": path.name,
    }

    magnetogram = _read_magnetogram(path, source=source, realization=realization, provenance=provenance)
    magnetogram = _normalise_projection(magnetogram, source=source, provenance=provenance)
    magnetogram = _resample(magnetogram, target_shape, provenance=provenance)
    magnetogram = _patch_non_finite(magnetogram, provenance=provenance)

    _record_provenance(magnetogram, provenance, frame_time=frame_time)
    return magnetogram, provenance


def _repair_sine_latitude_header(header: Any) -> bool:
    """Convert an HMI-style sine-latitude CEA header into a compliant one.

    Returns whether anything was changed.

    Changing ``CUNIT2`` alone would be a silent scientific error: the header's
    ``CDELT2`` is a spacing in sin(latitude), so relabelling the axis as degrees
    without rescaling leaves the map with a latitude scale wrong by a factor of
    180/pi. The correction is the one in Thompson (2006) section 5.5, and it is
    what both sunpy and ``pfss.utils.fix_hmi_meta`` apply:

        cdelt2 -> (180/pi) * cdelt2     and     cdelt1 -> |cdelt1|

    ``CUNIT1`` is separately non-compliant on these maps ("Degree" rather than
    "deg") and is normalised at the same time.
    """
    changed = False

    unit1 = str(header.get("CUNIT1", "") or "").strip().lower()
    if unit1 in ("degree", "degrees"):
        header["CUNIT1"] = "deg"
        changed = True

    unit2 = str(header.get("CUNIT2", "") or "").strip().lower()
    if unit2 in ("sine latitude", "sine_latitude", "sinlat"):
        header["CUNIT2"] = "deg"
        try:
            header["CDELT2"] = float(np.degrees(1.0)) * float(header["CDELT2"])
            header["CDELT1"] = abs(float(header["CDELT1"]))
        except Exception:
            return False
        changed = True
    return changed


def _read_with_repaired_units(path: Path) -> Any:
    """Second attempt at a map whose header carries an unparseable CUNIT.

    HMI synoptic maps declare ``CUNIT2 = 'Sine Latitude'``, which is a
    description rather than a unit and makes astropy refuse the header. sunpy
    only forgives this for files it recognises as HMI, which needs *both*
    ``TELESCOP`` ending in HMI and ``CONTENT`` containing "carrington synoptic
    chart" -- so a synoptic map that has been re-saved, or fetched from a mirror
    that dropped those keys, fails with an astropy unit error that says nothing
    about magnetograms.

    Verified against a live JSOC export of hmi.synoptic_mr_polfil_720s.

    Returns ``None`` if the file is unreadable for any other reason, so the
    original error is the one reported.
    """
    import sunpy.map
    from astropy.io import fits

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with fits.open(str(path)) as hdus:
                for hdu in hdus:
                    if hdu.data is None or np.ndim(hdu.data) != 2:
                        continue
                    header = hdu.header.copy()
                    if not _repair_sine_latitude_header(header):
                        continue
                    return sunpy.map.Map(np.asarray(hdu.data, dtype=float), header)
    except Exception:
        return None
    return None


def _read_magnetogram(
    path: Path, *, source: str, realization: int, provenance: dict[str, Any]
) -> Any:
    """Read the file into a single sunpy map, splitting ADAPT realizations."""
    import sunpy.map

    if source == SOURCE_ADAPT or _looks_like_adapt(path):
        maps = load_adapt_realizations(path)
        if not maps:
            raise MagnetogramError(f"No ADAPT realizations could be read from {path.name}.")
        index = int(np.clip(int(realization), 0, len(maps) - 1))
        provenance["realization"] = index
        provenance["realizations"] = len(maps)
        return maps[index]

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            loaded = sunpy.map.Map(str(path))
    except Exception as exc:
        loaded = _read_with_repaired_units(path)
        if loaded is None:
            raise MagnetogramError(f"Could not read {path.name} as a map: {exc}") from exc
        provenance["header_units_repaired"] = True

    if isinstance(loaded, list):
        if not loaded:
            raise MagnetogramError(f"{path.name} contains no usable map.")
        loaded = loaded[0]
    return loaded


def _looks_like_adapt(path: Path) -> bool:
    """Whether a filename looks like an ADAPT product."""
    name = path.name.lower()
    return name.startswith("adapt") or ".fts" in name and "adapt" in name


def load_adapt_realizations(path: str | Path) -> list[Any]:
    """Split an ADAPT file's 12 flux-transport realizations into maps.

    ADAPT stores its realizations along a third FITS axis, which
    ``sunpy.map.Map`` will not accept, so each slice is paired with the shared
    header by hand -- the approach the sunpy gallery documents.
    ``pfss.utils.load_adapt`` does the same thing when available; this fallback
    keeps the reader working when the optional PFSS stack is absent, so an ADAPT
    file can still be previewed.
    """
    import sunpy.map
    from astropy.io import fits

    path = Path(path).expanduser()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fits.open(str(path)) as hdus:
            primary = hdus[0]
            data = np.asarray(primary.data)
            header = primary.header.copy()
            if data.ndim == 2:
                pairs = [(data, header)]
            elif data.ndim == 3:
                pairs = [(np.asarray(plane), header) for plane in data]
            else:
                raise MagnetogramError(
                    f"{path.name} has {data.ndim}-dimensional data, which is not an ADAPT map."
                )
            built = sunpy.map.Map(pairs)
    return list(built) if isinstance(built, (list, tuple)) else [built]


def _normalise_projection(magnetogram: Any, *, source: str, provenance: dict[str, Any]) -> Any:
    """Repair metadata and reproject to cylindrical equal area if needed."""
    try:
        from sunkit_magex.pfss import utils as pfss_utils
    except Exception:
        # Without the PFSS stack there is nothing to normalise for; hand the map
        # back so callers that only want to preview it still work.
        provenance["projection"] = str(
            (getattr(magnetogram, "meta", {}) or {}).get("ctype1", "unknown")
        )
        return magnetogram

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if source == SOURCE_HMI:
            # fix_hmi_meta accepts only sunpy HMIMap instances, and its own
            # docstring notes it is redundant from sunpy 2.1 on, which applies
            # the same corrections when it recognises the file as HMI. It is
            # still called for maps that are recognised, because it is the
            # sanctioned path; anything else has already been repaired on read.
            import sunpy.map.sources

            if isinstance(magnetogram, sunpy.map.sources.HMIMap):
                try:
                    magnetogram = pfss_utils.fix_hmi_meta(magnetogram) or magnetogram
                    provenance["hmi_meta_fixed"] = True
                except Exception:
                    provenance["hmi_meta_fixed"] = False
            else:
                provenance["hmi_meta_fixed"] = bool(provenance.get("header_units_repaired"))

        try:
            is_cea = bool(pfss_utils.is_cea_map(magnetogram))
        except Exception:
            is_cea = False
        try:
            is_car = bool(pfss_utils.is_car_map(magnetogram))
        except Exception:
            is_car = False

        provenance["projection"] = "CEA" if is_cea else ("CAR" if is_car else "other")
        if not is_cea and is_car:
            # Reprojection builds a fresh header and does not carry BUNIT across,
            # which would leave every ADAPT map's field strength unlabelled in the
            # provenance line and on the diagnostics colorbar.
            units = str((getattr(magnetogram, "meta", {}) or {}).get("bunit", "") or "")
            try:
                reprojected = pfss_utils.car_to_cea(magnetogram)
            except Exception as exc:
                raise MagnetogramError(
                    f"Could not reproject this plate-carree map to equal area: {exc}"
                ) from exc
            if units and not (reprojected.meta or {}).get("bunit"):
                try:
                    reprojected.meta["bunit"] = units
                except Exception:
                    pass
            magnetogram = reprojected
            provenance["reprojected_to_cea"] = True
    return magnetogram


def _resample(magnetogram: Any, target_shape: tuple[int, int] | None, *, provenance: dict[str, Any]) -> Any:
    """Bin an oversampled synoptic map down to the PFSS grid."""
    import astropy.units as u

    shape = tuple(np.shape(magnetogram.data))
    provenance["original_shape"] = shape
    if target_shape is None:
        return magnetogram

    want_lon, want_lat = int(target_shape[0]), int(target_shape[1])
    if shape[1] <= want_lon and shape[0] <= want_lat:
        return magnetogram
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            resampled = magnetogram.resample([want_lon, want_lat] * u.pix)
        provenance["resampled_to"] = (want_lat, want_lon)
        return resampled
    except Exception:
        # Not fatal: a larger grid only costs solve time.
        return magnetogram


def _patch_non_finite(magnetogram: Any, *, provenance: dict[str, Any]) -> Any:
    """Replace NaN/inf pixels with zero.

    ``pfss.Input`` rejects any non-finite value outright, and GONG synoptic maps
    routinely carry NaN over the poles where the map is most foreshortened. Zero
    is the honest filler for an unmeasured pixel: it adds no flux. The count is
    recorded so a badly gapped map shows up in the provenance rather than
    quietly degrading the solution.
    """
    import sunpy.map

    data = np.asarray(magnetogram.data, dtype=float)
    bad = ~np.isfinite(data)
    count = int(np.count_nonzero(bad))
    provenance["non_finite_pixels"] = count
    provenance["non_finite_fraction"] = float(count / data.size) if data.size else 0.0
    if count == 0:
        return magnetogram

    patched = np.where(bad, 0.0, data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return sunpy.map.Map(patched, magnetogram.meta)


def _record_provenance(magnetogram: Any, provenance: dict[str, Any], *, frame_time: Any) -> None:
    """Fill in the audit trail: shape, rotation, date and staleness."""
    provenance["shape"] = tuple(np.shape(magnetogram.data))

    meta = getattr(magnetogram, "meta", {}) or {}
    obstime = _as_datetime(getattr(magnetogram, "date", None))
    provenance["obstime"] = obstime.isoformat() if obstime else ""

    rotation = None
    for key in ("car_rot", "CAR_ROT", "crot", "CROT"):
        rotation = _safe_int(meta.get(key))
        if rotation is not None:
            break
    if rotation is None and obstime is not None:
        rotation = carrington_rotation(obstime)
    provenance["carrington_rotation"] = rotation

    frame_stamp = _as_datetime(frame_time)
    if frame_stamp is not None and obstime is not None:
        try:
            delta_hours = (frame_stamp - obstime).total_seconds() / 3600.0
            provenance["frame_offset_hours"] = float(delta_hours)
        except Exception:
            pass

    data = np.asarray(magnetogram.data, dtype=float)
    if data.size:
        provenance["br_min"] = float(np.nanmin(data))
        provenance["br_max"] = float(np.nanmax(data))
        # Residual net flux. Physically it should vanish; pfss() excludes the
        # monopole term itself, so this is reported as a quality indicator and
        # deliberately not corrected here.
        mean = float(np.nanmean(data))
        peak = float(np.nanmax(np.abs(data))) or 1.0
        provenance["net_flux_mean"] = mean
        provenance["net_flux_ratio"] = abs(mean) / peak
    provenance["bunit"] = str(meta.get("bunit", "") or "")


def describe_magnetogram(provenance: dict[str, Any]) -> str:
    """One compact line for the sidebar hint.

    Leads with the time offset from the displayed frame, because that -- not the
    file name -- is what tells the user whether to trust the overlay.
    """
    if not provenance:
        return "No magnetogram loaded."

    bits: list[str] = [str(provenance.get("source_label") or "Magnetogram")]
    rotation = provenance.get("carrington_rotation")
    if rotation:
        bits.append(f"CR {rotation}")
    obstime = str(provenance.get("obstime") or "")
    if obstime:
        bits.append(obstime.replace("T", " ")[:16])

    shape = provenance.get("shape")
    if shape:
        bits.append(f"{shape[1]}x{shape[0]}")

    offset = provenance.get("frame_offset_hours")
    if isinstance(offset, (int, float)) and np.isfinite(offset):
        days = abs(float(offset)) / 24.0
        if days >= 1.0:
            bits.append(f"{days:.1f} d from frame")
        else:
            bits.append(f"{abs(float(offset)):.1f} h from frame")

    gaps = int(provenance.get("non_finite_pixels") or 0)
    if gaps:
        fraction = 100.0 * float(provenance.get("non_finite_fraction") or 0.0)
        bits.append(f"{gaps} gap px ({fraction:.1f}%) zero-filled")

    return " - ".join(bits)
