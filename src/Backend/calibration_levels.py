"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Processing-level selection for the Solar Image Analysis window.

Downloads always fetch the archive's *base* product — ``aia.lev1_euv_12s`` for
AIA, L1b for GOES/SUVI — and the level actually visualised is chosen afterwards
in the analyzer. Two different things can produce a higher level, and the
distinction matters scientifically, so it is modelled explicitly:

* ``ORIGIN_ARCHIVE`` — the level is a product the archive publishes *and can be
  queried for*. Switching to it means running a new query and downloading
  different files. GOES/SUVI L1b vs L2 is the case that qualifies: the two
  return genuinely different record sets.
* ``ORIGIN_LOCAL`` — the level is produced here, from frames already in hand.
  AIA level 1.5 is the only such level, and it is the *standard* way to obtain
  it: JSOC publishes no ``aia.lev1p5`` series, so every AIA user runs the prep
  pipeline themselves, historically via SolarSoft ``aia_prep`` and now via
  ``aiapy.calibrate``. The label shown to the user says which of the two it is.

No level is invented. In particular there is no AIA "level 2" — degradation
correction and exposure normalisation are real operations but they are not a
level, and they stay separate, explicitly named tools rather than being dressed
up as one.

Pure functions with injectable calibration callables (no Qt), so the whole
module is unit-testable without network access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


ORIGIN_ARCHIVE = "archive"
ORIGIN_LOCAL = "local"

# Level keys are canonical lowercase strings matching the archives' own
# vocabulary ("1", "1.5", "1b", "2", "0.5"). See _normalize_level_key.
LEVEL_1 = "1"
LEVEL_1_5 = "1.5"
LEVEL_1B = "1b"
LEVEL_2 = "2"


@dataclass(frozen=True)
class LevelOption:
    """One selectable processing level for a given instrument."""

    key: str
    label: str
    origin: str
    description: str = ""

    @property
    def is_local(self) -> bool:
        return self.origin == ORIGIN_LOCAL


@dataclass(frozen=True)
class LevelResult:
    """Outcome of :func:`apply_level`.

    ``warnings`` carries user-facing notes about anything that was degraded
    rather than failed — most importantly a level 1.5 produced without the
    3-hourly master pointing table, which is *not* the same product as one
    produced with it and must be reported rather than silently accepted.
    """

    frames: list[Any]
    level: str
    warnings: list[str] = field(default_factory=list)
    pointing_applied: bool = False
    cancelled: bool = False


_AIA_LEVELS: tuple[LevelOption, ...] = (
    LevelOption(
        key=LEVEL_1,
        label="Level 1 (as downloaded)",
        origin=ORIGIN_ARCHIVE,
        description=(
            "The archive product, straight from JSOC/VSO (aia.lev1_euv_12s).\n"
            "Bad pixels and spikes removed, but not rotated, not scaled to a\n"
            "common plate scale and not centred on the Sun."
        ),
    ),
    LevelOption(
        key=LEVEL_1_5,
        label="Level 1.5 (registered here)",
        origin=ORIGIN_LOCAL,
        description=(
            "Computed on this machine with aiapy, the Python equivalent of\n"
            "SolarSoft aia_prep. JSOC publishes no level-1.5 series, so this is\n"
            "the standard way every AIA user obtains it.\n"
            "Applies the 3-hourly master pointing, rotates CROTA2 to zero,\n"
            "scales to 0.6 arcsec/pixel and centres the Sun on the image."
        ),
    ),
)

_SUVI_LEVELS: tuple[LevelOption, ...] = (
    LevelOption(
        key=LEVEL_1B,
        label="Level 1b (as downloaded)",
        origin=ORIGIN_ARCHIVE,
        description="Single-exposure calibrated image, as served by NOAA.",
    ),
    LevelOption(
        key=LEVEL_2,
        label="Level 2 (archive composite)",
        origin=ORIGIN_ARCHIVE,
        description=(
            "NOAA's own high-dynamic-range composite. It is a different set of\n"
            "files, so selecting it re-queries the archive and downloads them."
        ),
    ),
)

# Keyed by an uppercase instrument token found in the frame's instrument name.
# Instruments absent from this table expose no level choice at all:
#   * HMI — hmi.M_720s and friends are already science-ready, with no
#     alternative level to choose.
#   * STEREO/SECCHI — secchi_prep is SolarSoft only, and the VSO serves one level.
#   * SOHO/LASCO — the archive does hold level-0.5 and level-1 products, but the
#     VSO does not filter on them: a search for the same window returns an
#     identical record set for a.Level(0.5), a.Level(1) and no Level at all
#     (verified against the live VSO). A selector here would look like it worked
#     and change nothing, so none is offered.
LEVEL_OPTIONS: dict[str, tuple[LevelOption, ...]] = {
    "AIA": _AIA_LEVELS,
    "SUVI": _SUVI_LEVELS,
}

_INSTRUMENT_TOKENS: tuple[tuple[str, str], ...] = (
    ("AIA", "AIA"),
    ("SUVI", "SUVI"),
    ("SOLAR ULTRAVIOLET IMAGER", "SUVI"),
)


def instrument_token(frame: Any) -> str:
    """Uppercase key into :data:`LEVEL_OPTIONS` for a loaded frame, or ``""``."""
    instrument = _text(getattr(frame, "instrument", None)) or _meta_text(
        frame, "instrume", "instrument"
    )
    haystack = instrument.upper()
    for needle, token in _INSTRUMENT_TOKENS:
        if needle in haystack:
            return token
    return ""


def levels_for_frame(frame: Any) -> tuple[LevelOption, ...]:
    """Selectable levels for ``frame``'s instrument (empty when it has none)."""
    return LEVEL_OPTIONS.get(instrument_token(frame), ())


def find_level_option(frame: Any, level: str) -> LevelOption | None:
    key = _normalize_level_key(level)
    for option in levels_for_frame(frame):
        if option.key == key:
            return option
    return None


def detect_frame_level(frame: Any) -> str | None:
    """Processing level recorded in the frame's own header, if it says.

    ``LVL_NUM`` is the SDO convention and is authoritative for AIA — ``aiapy``'s
    ``register`` rewrites it to 1.5, so a re-registered frame identifies itself.
    ``LEVEL`` is the fallback other missions use. Returns ``None`` when the
    header is silent rather than guessing.
    """
    for key in ("lvl_num", "level"):
        raw = _meta_value(frame, key)
        if raw is None:
            continue
        normalized = _normalize_level_key(raw)
        if normalized:
            return normalized
    return None


def requires_download(option: LevelOption, base_level: str | None) -> bool:
    """Whether reaching ``option`` means going back to the archive.

    Anything derivable locally does not, and neither does the level we already
    downloaded — the analyzer keeps those frames pristine and can simply restore
    them. Every other archive level is a different set of files.
    """
    if option.is_local:
        return False
    base = _normalize_level_key(base_level) if base_level else ""
    return bool(base) and option.key != base


def apply_level(
    frames: Sequence[Any],
    level: str,
    *,
    base_level: str | None = None,
    progress_cb: Callable[[int, int], Any] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    register_fn: Any | None = None,
    update_pointing_fn: Any | None = None,
    pointing_table: Any | None = None,
    pointing_table_fetcher: Callable[..., Any] | None = None,
) -> LevelResult:
    """Produce ``frames`` at the requested processing ``level``.

    Only locally derivable levels do any work here; an archive level that is not
    the one already downloaded is the caller's problem (see
    :func:`requires_download`) and raises rather than silently returning the
    wrong data.

    Every calibration callable is injectable so the pipeline can be exercised in
    tests without ``aiapy`` or a network connection.
    """
    work = list(frames)
    if not work:
        raise ValueError("Load solar frames before changing the processing level.")

    target = _normalize_level_key(level)
    if not target:
        raise ValueError(f"Unrecognised processing level: {level!r}")

    base = _normalize_level_key(base_level) if base_level else _normalize_level_key(
        detect_frame_level(work[0]) or ""
    )
    if target == base:
        # Already the level in hand — the caller restores its pristine frames.
        return LevelResult(frames=work, level=target)

    if target != LEVEL_1_5:
        raise ValueError(
            f"Level {target} is an archive product for this instrument and cannot be "
            "produced from the loaded frames. Re-run the archive search at that level."
        )

    return register_level_1_5(
        work,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
        register_fn=register_fn,
        update_pointing_fn=update_pointing_fn,
        pointing_table=pointing_table,
        pointing_table_fetcher=pointing_table_fetcher,
    )


def register_level_1_5(
    frames: Sequence[Any],
    *,
    use_pointing: bool = True,
    progress_cb: Callable[[int, int], Any] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    register_fn: Any | None = None,
    update_pointing_fn: Any | None = None,
    pointing_table: Any | None = None,
    pointing_table_fetcher: Callable[..., Any] | None = None,
) -> LevelResult:
    """AIA level 1 -> 1.5: master pointing update, then registration.

    This is the single ``aiapy`` prep pipeline in the project; the older
    :func:`src.Backend.solar_data_analysis.register_aia_maps` delegates here with
    ``use_pointing=False`` so there is one implementation to reason about.

    Set ``use_pointing=False`` to skip the master pointing lookup entirely — the
    geometric registration is then done offline, with no network access at all.
    """
    frames = list(frames)
    if register_fn is None:
        try:
            from aiapy.calibrate import register as register_fn
        except Exception as exc:  # pragma: no cover - exercised only without aiapy
            raise RuntimeError(
                "Level 1.5 needs the optional 'aiapy' package.\n"
                "Install it with: python3 -m pip install aiapy"
            ) from exc

    warnings: list[str] = []

    # The pointing update is the one step that needs the network. When it is not
    # available we still register — the geometry correction is the bulk of the
    # benefit — but the result is reported as the lesser product it is.
    if pointing_table is None and use_pointing:
        pointing_table, pointing_warning = _fetch_pointing_table(
            frames, fetcher=pointing_table_fetcher
        )
        if pointing_warning:
            warnings.append(pointing_warning)

    if update_pointing_fn is None and pointing_table is not None:
        try:
            from aiapy.calibrate import update_pointing as update_pointing_fn
        except Exception:  # pragma: no cover - aiapy present but partial
            update_pointing_fn = None
            warnings.append(
                "aiapy.calibrate.update_pointing is unavailable; frames were "
                "registered without the master pointing update."
            )

    out: list[Any] = []
    failures: list[str] = []
    pointing_applied = False
    total = len(frames)
    for index, frame in enumerate(frames):
        if _cancelled(cancel_cb):
            return LevelResult(
                frames=list(frames),
                level=LEVEL_1_5,
                warnings=warnings,
                pointing_applied=pointing_applied,
                cancelled=True,
            )

        prepped = frame
        if update_pointing_fn is not None and pointing_table is not None:
            try:
                prepped = update_pointing_fn(prepped, pointing_table=pointing_table)
                pointing_applied = True
            except Exception as exc:  # noqa: BLE001 - keep going without the update
                failures.append(f"pointing update on frame {index + 1}: {exc}")
        try:
            out.append(register_fn(prepped))
        except Exception as exc:  # noqa: BLE001 - one bad frame must not lose the set
            failures.append(f"registration of frame {index + 1}: {exc}")
            out.append(frame)

        _report(progress_cb, index + 1, total)

    if failures:
        warnings.append(
            f"{len(failures)} step(s) fell back to the input frame: " + "; ".join(failures[:3])
        )
    if use_pointing and not pointing_applied and not any("pointing" in w for w in warnings):
        warnings.append(
            "Frames were registered without the 3-hourly master pointing update."
        )

    return LevelResult(
        frames=out,
        level=LEVEL_1_5,
        warnings=warnings,
        pointing_applied=pointing_applied,
    )


# The master pointing series is 3-hourly, so the query window is padded to
# guarantee a record bracketing every frame in the sequence.
_POINTING_PAD_HOURS = 3.0

_pointing_cache: dict[tuple[str, str], Any] = {}


def _fetch_pointing_table(
    frames: Sequence[Any], *, fetcher: Callable[..., Any] | None = None
) -> tuple[Any | None, str]:
    """Master pointing table covering ``frames``, or ``(None, reason)``.

    Only the ``'jsoc'`` source is ever requested: ``get_pointing_table('lmsal')``
    raises ``TypeError: ... unexpected keyword argument 'overwrite'`` on the
    pinned aiapy 0.10.2 / parfive 2.3.1 pair, so it is not a usable fallback.
    """
    try:
        from astropy.time import TimeDelta
        import astropy.units as u
        from sunpy.time import TimeRange
    except Exception as exc:  # pragma: no cover - astropy/sunpy always present
        return None, f"Pointing table unavailable ({exc})."

    times = [t for t in (_frame_time(frame) for frame in frames) if t is not None]
    if not times:
        return None, (
            "Frames carry no readable observation time, so the master pointing "
            "table could not be looked up."
        )

    pad = TimeDelta(_POINTING_PAD_HOURS * u.hour)
    start = min(times) - pad
    end = max(times) + pad
    cache_key = (start.isot[:13], end.isot[:13])
    if cache_key in _pointing_cache:
        return _pointing_cache[cache_key], ""

    if fetcher is None:
        try:
            from aiapy.calibrate.util import get_pointing_table as fetcher
        except Exception as exc:
            return None, f"Pointing table unavailable ({exc})."

    try:
        table = fetcher("jsoc", time_range=TimeRange(start, end))
    except Exception as exc:  # noqa: BLE001 - offline is a normal, recoverable state
        return None, (
            f"Could not fetch the master pointing table from JSOC ({exc}). "
            "Frames were registered without the pointing update."
        )

    _pointing_cache[cache_key] = table
    return table, ""


def clear_pointing_cache() -> None:
    """Drop cached pointing tables (used by tests and the cache-clear action)."""
    _pointing_cache.clear()


def _frame_time(frame: Any) -> Any:
    """Observation time of ``frame`` as an astropy ``Time``, or ``None``."""
    date = getattr(frame, "date", None)
    if date is None:
        return None
    if hasattr(date, "isot"):
        return date
    try:
        from astropy.time import Time

        return Time(str(date))
    except Exception:
        return None


def _normalize_level_key(value: Any) -> str:
    """Canonical level key: ``1`` / ``1.5`` / ``1b`` / ``2`` / ``0.5``.

    Accepts the many spellings the archives and headers use — ``"L1b"``,
    ``"1.0"``, the float ``1.0``, ``"Level 2"`` — and reduces them to the one
    form the tables are keyed by.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return _format_number(float(value))

    text = str(value).strip().lower()
    if not text:
        return ""
    for prefix in ("level", "lev", "l"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip(" _-")
            break
    if not text:
        return ""
    try:
        return _format_number(float(text))
    except ValueError:
        # Non-numeric levels such as "1b" keep their letter suffix.
        return text


def _format_number(value: float) -> str:
    """``1.0`` -> ``"1"``, ``1.5`` -> ``"1.5"``, ``2.0`` -> ``"2"``."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _cancelled(cancel_cb: Callable[[], bool] | None) -> bool:
    if cancel_cb is None:
        return False
    try:
        return bool(cancel_cb())
    except Exception:
        return False


def _report(progress_cb: Callable[[int, int], Any] | None, done: int, total: int) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(int(done), int(total))
    except Exception:
        pass


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _meta_value(frame: Any, key: str) -> Any:
    meta = getattr(frame, "meta", None)
    if not meta:
        return None
    try:
        lowered = {str(k).strip().lower(): v for k, v in dict(meta).items()}
    except Exception:
        return None
    return lowered.get(key.lower())


def _meta_text(frame: Any, *keys: str) -> str:
    for key in keys:
        value = _meta_value(frame, key)
        if value is not None:
            return _text(value)
    return ""
