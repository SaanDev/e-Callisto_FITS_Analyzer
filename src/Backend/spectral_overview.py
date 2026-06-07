"""
Full-day e-CALLISTO spectral overview processing and rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import os
import tempfile
from typing import Callable, Iterable

import matplotlib as mpl
import numpy as np
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from src.Backend.batch_processing import PLOTUTIL_DB_SCALE, PLOTUTIL_DISPLAY_LIMITS
from src.Backend.fits_io import load_callisto_fits, preview_callisto_fits
from src.Backend.frequency_axis import (
    frequency_edges,
    masked_display_data,
    orient_frequency_axis,
    orient_frequency_rows,
    transparent_bad_cmap,
)


FREQUENCY_MATCH_ATOL_MHZ = 1e-3
DEFAULT_PANEL_RENDER_COLUMNS = 1440
SECONDS_PER_DAY = 24 * 60 * 60
SECONDS_PER_PANEL = 4 * 60 * 60


class SpectralOverviewCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class SpectralOverviewSource:
    path: str
    station: str
    observed_at_utc: datetime
    focus_code: str
    filename: str = ""


@dataclass(frozen=True)
class SpectralOverviewSegment:
    source: SpectralOverviewSource
    data_db: np.ndarray
    freqs: np.ndarray
    utc_seconds: np.ndarray
    frequency_group: int


@dataclass(frozen=True)
class SpectralOverviewResult:
    station: str
    observation_date: date
    focus_code: str
    segments: tuple[SpectralOverviewSegment, ...]
    total_sources: int
    loaded_sources: int
    coverage_seconds: float
    warnings: tuple[str, ...] = ()


@dataclass
class _FrequencyGroup:
    freqs: np.ndarray
    members: list[tuple[SpectralOverviewSource, int]]


def _check_cancel(cancel_check: Callable[[], bool] | None) -> None:
    if callable(cancel_check) and bool(cancel_check()):
        raise SpectralOverviewCancelled("Spectral overview generation was cancelled.")


def _report(progress_callback: Callable[[str], None] | None, message: str) -> None:
    if callable(progress_callback):
        progress_callback(str(message))


def _close_memmap(memmap_array: np.memmap | None) -> None:
    if memmap_array is None:
        return
    try:
        memmap_array.flush()
    except Exception:
        pass
    mmap_handle = getattr(memmap_array, "_mmap", None)
    if mmap_handle is not None:
        try:
            mmap_handle.close()
        except Exception:
            pass


def _compatible_frequency_group(groups: list[_FrequencyGroup], freqs: np.ndarray) -> _FrequencyGroup | None:
    freq_arr = np.asarray(freqs, dtype=float).ravel()
    for group in groups:
        if group.freqs.shape == freq_arr.shape and np.allclose(
            group.freqs,
            freq_arr,
            atol=FREQUENCY_MATCH_ATOL_MHZ,
            rtol=0.0,
            equal_nan=False,
        ):
            return group
    return None


def _seconds_of_day(value: datetime) -> float:
    return float(value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000.0)


def _peak_preserving_downsample(
    data: np.ndarray,
    utc_seconds: np.ndarray,
    *,
    max_columns: int,
) -> tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(data, dtype=np.float32)
    times = np.asarray(utc_seconds, dtype=float).ravel()
    if arr.shape[1] <= max_columns:
        return arr.astype(np.float32, copy=True), times.astype(float, copy=True)

    count = max(1, int(max_columns))
    boundaries = np.linspace(0, arr.shape[1], count + 1, dtype=int)
    out = np.full((arr.shape[0], count), np.nan, dtype=np.float32)
    out_times = np.empty(count, dtype=float)

    for idx in range(count):
        start = int(boundaries[idx])
        stop = max(start + 1, int(boundaries[idx + 1]))
        block = arr[:, start:stop]
        finite_rows = np.any(np.isfinite(block), axis=1)
        if np.any(finite_rows):
            out[finite_rows, idx] = np.nanmax(block[finite_rows, :], axis=1).astype(np.float32)
        out_times[idx] = float(np.nanmean(times[start:stop]))

    return out, out_times


def _coverage_seconds(segments: Iterable[SpectralOverviewSegment]) -> float:
    intervals: list[tuple[float, float]] = []
    for segment in segments:
        times = np.asarray(segment.utc_seconds, dtype=float).ravel()
        if times.size == 0:
            continue
        start = float(np.nanmin(times))
        stop = float(np.nanmax(times))
        if times.size > 1:
            diffs = np.diff(times)
            diffs = diffs[np.isfinite(diffs) & (diffs > 0.0)]
            if diffs.size:
                stop += float(np.nanmedian(diffs))
        intervals.append((max(0.0, start), min(float(SECONDS_PER_DAY), stop)))

    if not intervals:
        return 0.0
    intervals.sort()
    merged: list[list[float]] = []
    for start, stop in intervals:
        if stop <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, stop])
        else:
            merged[-1][1] = max(merged[-1][1], stop)
    return float(sum(stop - start for start, stop in merged))


def build_spectral_overview(
    sources: Iterable[SpectralOverviewSource],
    *,
    temp_dir: str | None = None,
    panel_render_columns: int = DEFAULT_PANEL_RENDER_COLUMNS,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> SpectralOverviewResult:
    source_list = sorted(list(sources), key=lambda item: (item.observed_at_utc, item.filename or item.path))
    if not source_list:
        raise ValueError("At least one FITS source is required.")

    station = source_list[0].station
    observation_date = source_list[0].observed_at_utc.date()
    focus_code = source_list[0].focus_code
    for source in source_list[1:]:
        if (
            str(source.station).strip().casefold() != str(station).strip().casefold()
            or source.observed_at_utc.date() != observation_date
            or str(source.focus_code).strip() != str(focus_code).strip()
        ):
            raise ValueError("Spectral overview sources must share one station, UTC date, and focus code.")
    groups: list[_FrequencyGroup] = []
    warnings: list[str] = []

    _report(progress_callback, "Inspecting FITS frequency grids...")
    for source in source_list:
        _check_cancel(cancel_check)
        try:
            preview = preview_callisto_fits(source.path, memmap=False)
            freqs = orient_frequency_axis(preview.freqs, direction=-1)
            if preview.freq_source == "index":
                raise ValueError("missing frequency metadata")
            if freqs.size == 0 or int(preview.data_shape[1]) <= 0:
                raise ValueError("missing frequency or time axis")
            group = _compatible_frequency_group(groups, freqs)
            if group is None:
                group = _FrequencyGroup(freqs=np.asarray(freqs, dtype=float), members=[])
                groups.append(group)
            group.members.append((source, int(preview.data_shape[1])))
        except Exception as exc:
            warnings.append(f"{source.filename or os.path.basename(source.path)}: {exc}")

    if not groups:
        raise ValueError("No readable FITS sources were available for the overview.")

    os.makedirs(temp_dir, exist_ok=True) if temp_dir else None
    segments: list[SpectralOverviewSegment] = []
    loaded_source_paths: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="spectral_overview_", dir=temp_dir) as work_dir:
        for group_index, group in enumerate(groups):
            _check_cancel(cancel_check)
            total_columns = sum(max(0, int(columns)) for _source, columns in group.members)
            if total_columns <= 0:
                continue

            _report(
                progress_callback,
                f"Building day-wide median for frequency configuration {group_index + 1}/{len(groups)}...",
            )
            mmap_path = os.path.join(work_dir, f"frequency_group_{group_index}.dat")
            day_data: np.memmap | None = np.memmap(
                mmap_path,
                dtype=np.float32,
                mode="w+",
                shape=(group.freqs.size, total_columns),
            )
            day_data[:] = np.nan
            write_offset = 0
            valid_members: list[SpectralOverviewSource] = []
            try:
                for source, expected_columns in group.members:
                    _check_cancel(cancel_check)
                    try:
                        result = load_callisto_fits(source.path, memmap=False)
                        data, freqs = orient_frequency_rows(result.data, result.freqs, direction=-1)
                        data = np.asarray(data, dtype=np.float32)
                        freqs = np.asarray(freqs, dtype=float).ravel()
                        if data.shape[0] != group.freqs.size or not np.allclose(
                            freqs,
                            group.freqs,
                            atol=FREQUENCY_MATCH_ATOL_MHZ,
                            rtol=0.0,
                        ):
                            raise ValueError("frequency grid changed while loading")
                        count = min(int(expected_columns), int(data.shape[1]), total_columns - write_offset)
                        if count <= 0:
                            raise ValueError("FITS file contains no time samples")
                        day_data[:, write_offset : write_offset + count] = data[:, :count]
                        write_offset += int(expected_columns)
                        valid_members.append(source)
                        loaded_source_paths.add(source.path)
                    except Exception as exc:
                        warnings.append(f"{source.filename or os.path.basename(source.path)}: {exc}")
                        write_offset += int(expected_columns)

                day_data.flush()
                baseline = np.full((group.freqs.size, 1), np.nan, dtype=np.float32)
                for row in range(group.freqs.size):
                    _check_cancel(cancel_check)
                    # Copy each row so no memmap-backed view survives until
                    # TemporaryDirectory cleanup on Windows.
                    values = np.array(day_data[row, :], dtype=np.float32, copy=True)
                    if np.any(np.isfinite(values)):
                        baseline[row, 0] = np.float32(np.nanmedian(values))

                for source in valid_members:
                    _check_cancel(cancel_check)
                    try:
                        result = load_callisto_fits(source.path, memmap=False)
                        data, freqs = orient_frequency_rows(result.data, result.freqs, direction=-1)
                        data_db = (
                            (np.asarray(data, dtype=np.float32) - baseline) * np.float32(PLOTUTIL_DB_SCALE)
                        ).astype(np.float32, copy=False)
                        relative_time = np.asarray(result.time, dtype=float).ravel()
                        count = min(data_db.shape[1], relative_time.size)
                        data_db = data_db[:, :count]
                        relative_time = relative_time[:count]
                        utc_seconds = _seconds_of_day(source.observed_at_utc) + relative_time
                        inside = np.isfinite(utc_seconds) & (utc_seconds >= 0.0) & (utc_seconds < SECONDS_PER_DAY)
                        if not np.any(inside):
                            raise ValueError("FITS samples fall outside the selected UTC day")
                        data_db = data_db[:, inside]
                        utc_seconds = utc_seconds[inside]
                        duration = max(1.0, float(np.nanmax(utc_seconds) - np.nanmin(utc_seconds)))
                        max_columns = max(
                            1,
                            int(np.ceil(float(panel_render_columns) * duration / float(SECONDS_PER_PANEL))),
                        )
                        render_data, render_time = _peak_preserving_downsample(
                            data_db,
                            utc_seconds,
                            max_columns=max_columns,
                        )
                        segments.append(
                            SpectralOverviewSegment(
                                source=source,
                                data_db=render_data,
                                freqs=np.asarray(freqs, dtype=float).ravel(),
                                utc_seconds=render_time,
                                frequency_group=group_index,
                            )
                        )
                    except Exception as exc:
                        warnings.append(f"{source.filename or os.path.basename(source.path)}: {exc}")
            finally:
                _close_memmap(day_data)
                day_data = None

    if not segments:
        raise ValueError("No valid FITS data remained after processing.")

    segments.sort(key=lambda item: (float(item.utc_seconds[0]), item.frequency_group, item.source.filename))
    return SpectralOverviewResult(
        station=station,
        observation_date=observation_date,
        focus_code=focus_code,
        segments=tuple(segments),
        total_sources=len(source_list),
        loaded_sources=len(loaded_source_paths),
        coverage_seconds=_coverage_seconds(segments),
        warnings=tuple(warnings),
    )


def render_spectral_overview_figure(result: SpectralOverviewResult) -> Figure:
    if not result.segments:
        raise ValueError("The spectral overview contains no segments.")

    all_freqs = np.concatenate([np.asarray(segment.freqs, dtype=float).ravel() for segment in result.segments])
    finite_freqs = all_freqs[np.isfinite(all_freqs)]
    if finite_freqs.size == 0:
        raise ValueError("The spectral overview contains no valid frequency coordinates.")
    freq_min = float(np.nanmin(finite_freqs))
    freq_max = float(np.nanmax(finite_freqs))

    fig, axes = Figure(figsize=(22, 13), facecolor="white"), []
    grid = fig.add_gridspec(6, 1, left=0.065, right=0.87, bottom=0.065, top=0.88, hspace=0.20)
    cmap = transparent_bad_cmap(mpl.colormaps.get_cmap("viridis"))
    norm = mpl.colors.Normalize(vmin=float(PLOTUTIL_DISPLAY_LIMITS[0]), vmax=float(PLOTUTIL_DISPLAY_LIMITS[1]))

    for panel_index in range(6):
        ax = fig.add_subplot(grid[panel_index, 0])
        axes.append(ax)
        panel_start = float(panel_index * SECONDS_PER_PANEL)
        panel_stop = float((panel_index + 1) * SECONDS_PER_PANEL)
        rendered = False

        for segment in result.segments:
            times = np.asarray(segment.utc_seconds, dtype=float).ravel()
            if times.size == 0 or float(np.nanmax(times)) < panel_start or float(np.nanmin(times)) >= panel_stop:
                continue
            inside = (times >= panel_start) & (times < panel_stop)
            if not np.any(inside):
                continue
            panel_data = np.asarray(segment.data_db, dtype=np.float32)[:, inside]
            panel_times = times[inside]
            if panel_times.size == 1:
                x0 = float(panel_times[0])
                x1 = min(panel_stop, x0 + 1.0)
            else:
                x0 = float(panel_times[0])
                x1 = float(panel_times[-1])
            edges = frequency_edges(segment.freqs)
            ax.imshow(
                masked_display_data(panel_data),
                aspect="auto",
                extent=[x0, x1, float(edges[-1]), float(edges[0])],
                origin="upper",
                cmap=cmap,
                norm=norm,
                interpolation="nearest",
                rasterized=True,
            )
            rendered = True

        ax.set_xlim(panel_start, panel_stop)
        ax.set_ylim(freq_min, freq_max)
        ax.set_facecolor("white")
        ax.set_xticks(np.arange(panel_start, panel_stop, 3600.0))
        ax.set_xticks(np.arange(panel_start, panel_stop, 900.0), minor=True)
        ax.xaxis.set_major_formatter(
            FuncFormatter(lambda value, _pos: f"{int(value // 3600) % 24:02d}:00")
        )
        ax.grid(which="major", color="#59636e", linewidth=0.7, alpha=0.55)
        ax.grid(which="minor", color="#a8b0b8", linewidth=0.4, alpha=0.45)
        ax.tick_params(axis="both", labelsize=9)
        label_stop = (panel_index + 1) * 4
        ax.text(
            1.012,
            0.5,
            f"{panel_index * 4:02d}-{label_stop:02d} UT",
            transform=ax.transAxes,
            va="center",
            ha="left",
            fontsize=13,
        )
        if not rendered:
            ax.text(
                0.5,
                0.5,
                "No data",
                transform=ax.transAxes,
                ha="center",
                va="center",
                color="#7a828a",
                fontsize=13,
            )

    fig.suptitle(
        f"Full-day spectrum {result.observation_date:%Y-%m-%d}\n"
        f"Station: {result.station} | Focus code: {result.focus_code}",
        fontsize=18,
        y=0.98,
    )
    fig.text(0.020, 0.5, "Frequency [MHz]", va="center", rotation="vertical", fontsize=14)
    fig.text(0.49, 0.018, "Time [UTC]", ha="center", fontsize=14)
    scalar = mpl.cm.ScalarMappable(norm=norm, cmap=cmap)
    scalar.set_array([])
    colorbar_ax = fig.add_axes([0.935, 0.18, 0.014, 0.64])
    colorbar = fig.colorbar(scalar, cax=colorbar_ax)
    colorbar.set_label("Background-subtracted intensity [dB]")
    return fig
