"""Write the full app's results for the parity fixtures, for Lite's tests.

Runs the full app's own Python functions (not re-implementations) on the files
in ``lite/parity/fixtures`` and writes ``lite/parity/out/cases.json`` plus one
``.npy`` file per array. Each case has a ``kind`` the Rust test
``crates/callisto-core/tests/parity.rs`` knows how to replay, its inputs, and
the outputs (or the error message) the full app produced.

usage (from the repository root, in the full app's environment):
    python lite/parity/make_reference.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from astropy.io import fits  # noqa: E402

from src.backend.common.array_stats import row_quantiles  # noqa: E402
from src.backend.radio import burst_processor as bp  # noqa: E402
from src.backend.radio import callisto_archive, callisto_timeline  # noqa: E402
from src.backend.radio import frequency_axis as fa  # noqa: E402
from src.backend.radio.batch_processing import PLOTUTIL_DB_SCALE, convert_digits_to_db, subtract_background  # noqa: E402
from src.backend.radio.fits_io import extract_ut_start_sec, load_callisto_fits, preview_callisto_fits  # noqa: E402
from src.backend.radio.noise_reduction import rowwise_noise_scale, subtract_background_rows  # noqa: E402
from src.backend.radio.rfi_filters import clean_rfi  # noqa: E402

FIXTURES = Path(__file__).with_name("fixtures")
OUT = Path(__file__).with_name("out")
CASES: list[dict] = []


def save(name: str, arr) -> str:
    """Store an array as float64 .npy; return the file name."""
    fname = f"{name}.npy"
    np.save(OUT / fname, np.ascontiguousarray(np.asarray(arr, dtype=np.float64)))
    return fname


def case(name: str, kind: str, **fields) -> dict:
    entry = {"name": name, "kind": kind, **fields}
    CASES.append(entry)
    return entry


def fixture(name: str) -> str:
    return str(FIXTURES / name)


def header_values(header, keys) -> dict:
    out = {}
    for key in keys:
        if key in header:
            value = header[key]
            out[key] = value if isinstance(value, (bool, int, float, str)) else str(value)
    return out


def write_artemis_fixture() -> Path:
    """The full app's synthetic ARTLOOK file (tests/backend/test_backend_artemis.py)."""
    path = FIXTURES / "ARTEMIS_00714.FITS"
    if path.exists():
        return path
    hdr = fits.Header()
    hdr["SIMPLE"] = (True, "STANDARD FITS CREATED BY ARTLOOK")
    hdr["BITPIX"] = 16
    hdr["NAXIS"] = 2
    hdr["NAXIS1"] = 601
    hdr["NAXIS2"] = 128
    hdr["CTYPE1"] = "         TIME (UT)"
    hdr["CRPIX1"] = 1
    hdr["CRVAL1"] = 8.9999722222
    hdr["CDELT1"] = 0.0002777778
    hdr["CTYPE2"] = "   FREQUENCY (MHZ)"
    hdr["CRPIX2"] = 1
    hdr["CRVAL2"] = 686.6300048828
    hdr["CDELT2"] = -4.5403938293
    hdr["BUNIT"] = "            COUNTS"
    hdr["BLANK"] = -32767
    hdr["OBJECT"] = "               SUN"
    hdr["ORIGIN"] = "ARTEMIS-IV, THERMOPYLAE, GREECE"
    hdr["TELESCOP"] = " RADIOSPECTROGRAPH"
    hdr["INSTRUME"] = "               ASG"
    hdr["DATE"] = "2015-22-06T14:56:03.092"
    hdr["DATE-OBS"] = "2000-07-14T08:59:59.900"
    hdr["TIME-OBS"] = "      08:59:59.900"
    rng = np.random.default_rng(7)
    gains = np.linspace(20.0, 160.0, 128).reshape(-1, 1)
    data = gains + rng.normal(0.0, 3.0, size=(128, 601))
    data[40:80, 200:260] += 900.0
    fits.PrimaryHDU(data=data.astype(np.int16), header=hdr).writeto(path, overwrite=True, output_verify="ignore")
    return path


def try_call(fn):
    try:
        return fn(), None
    except Exception as exc:  # noqa: BLE001 - the message is the reference
        return None, str(exc)


def combined_case(name: str, kind: str, paths, result, error, **extra):
    fields = {"paths": [Path(p).name for p in paths], **extra}
    if error is not None:
        return case(name, kind, error=error, **fields)
    header_keys = ("COMBINED", "COMBMETH", "NFILES", "FREQMIN", "FREQMAX", "TMIN", "TMAX", "NAXIS1", "NAXIS2",
                   "CRVAL2", "CDELT2", "NTIMES", "NFOCUS")
    return case(
        name,
        kind,
        **fields,
        data=save(f"{name}.data", result["data"]),
        freqs=list(map(float, result["freqs"])),
        time_npy=save(f"{name}.time", result["time"]),
        filename=result["filename"],
        sources=[Path(p).name for p in result["sources"]],
        ut_start_sec=result["ut_start_sec"],
        gap_row_mask=None if result.get("gap_row_mask") is None else [bool(v) for v in result["gap_row_mask"]],
        frequency_step_mhz=result.get("frequency_step_mhz"),
        header=header_values(result["header0"], header_keys),
        history=[str(v) for v in result["header0"].get("HISTORY", [])],
    )


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for stale in OUT.glob("*"):
        stale.unlink()
    write_artemis_fixture()
    files = sorted(p for p in FIXTURES.iterdir() if p.name.lower().endswith((".fit", ".fits", ".fit.gz", ".fits.gz")))

    # --- loading and previews ------------------------------------------
    for path in files:
        result = load_callisto_fits(str(path))
        preview = preview_callisto_fits(str(path))
        case(
            f"load {path.name}",
            "load",
            file=path.name,
            data=save(f"load.{path.name}.data", result.data),
            freqs=list(map(float, result.freqs)),
            time=list(map(float, result.time)),
            ut_start_sec=extract_ut_start_sec(result.header0),
            preview_shape=list(preview.data_shape),
            preview_freqs=list(map(float, preview.freqs)),
            preview_time=list(map(float, preview.time)),
            freq_source=preview.freq_source,
            time_source=preview.time_source,
        )

    # --- combining ------------------------------------------------------
    gauri = [fixture("INDIA-GAURI_20261007_041500_02.fit.gz"), fixture("INDIA-GAURI_20261007_043000_02.fit.gz")]
    dlr0500 = [fixture(f"GERMANY-DLR_20261007_050000_{f}.fit.gz") for f in ("01", "02", "03")]
    dlr0515 = [fixture(f"GERMANY-DLR_20261007_051500_{f}.fit.gz") for f in ("01", "02", "03")]
    selections = {
        "time pair": gauri,
        "time pair reversed": gauri[::-1],
        "frequency triple": dlr0500,
        "time x frequency": dlr0500 + dlr0515,
        "one file": gauri[:1],
        "mixed stations": [gauri[0], dlr0500[0]],
        "not consecutive": [gauri[0], fixture("INDIA-GAURI_20261007_050000_02.fit.gz")],
        "duplicate focus": [dlr0500[0], dlr0500[0].replace("_01.", "_01.")],
        "incomplete grid": dlr0500 + dlr0515[:2],
    }
    for label, paths in selections.items():
        ins = bp.inspect_combination(paths)
        relation = ins.get("frequency_relation")
        case(
            f"inspect {label}",
            "inspect",
            paths=[Path(p).name for p in paths],
            valid=bool(ins.get("valid")),
            combine_type=ins.get("combine_type"),
            error=ins.get("error"),
            focus_codes=list(ins.get("focus_codes") or []),
            groups=[[Path(p).name for p in g] for g in ins.get("timestamp_groups") or []],
            gaps=[[g["low"], g["high"]] for g in (relation or {}).get("gaps", [])],
            overlaps=[[o["low"], o["high"]] for o in (relation or {}).get("overlaps", [])],
        )

    result, error = try_call(lambda: bp.combine_time(gauri))
    combined_case("combine time", "combine", gauri, result, error, options={})
    # DLR 01+02+03 overlap without a gap; DLR 02+01 leave 800-1047 MHz empty,
    # OE3FLB 57+55 leave 87-112 MHz, and TRIEST 57+59 mix 100 and 200 channels.
    pairs = {
        "triple": dlr0500,
        "dlr gap": [dlr0500[1], dlr0500[0]],
        "oe3flb gap": [fixture("AUSTRIA-OE3FLB_20261007_051500_57.fit.gz"), fixture("AUSTRIA-OE3FLB_20261007_051500_55.fit.gz")],
        "triest gap": [fixture("TRIEST_20261007_050000_57.fit.gz"), fixture("TRIEST_20261007_050000_59.fit.gz")],
    }
    for label, selection in pairs.items():
        for gap in ("background", "average", "hatched", "zero"):
            for overlap in ("split", "low", "high", "reject"):
                options = {"gap_fill": gap, "overlap_policy": overlap}
                result, error = try_call(lambda: bp.combine_frequency(selection, **options))
                combined_case(f"combine frequency {label} {gap} {overlap}", "combine", selection, result, error,
                              options=options)
    for label, selection in list(pairs.items())[1:]:
        ins = bp.inspect_combination(selection)
        relation = ins.get("frequency_relation") or {}
        case(f"inspect {label}", "inspect", paths=[Path(p).name for p in selection],
             valid=bool(ins.get("valid")), combine_type=ins.get("combine_type"), error=ins.get("error"),
             focus_codes=list(ins.get("focus_codes") or []),
             groups=[[Path(p).name for p in g] for g in ins.get("timestamp_groups") or []],
             gaps=[[g["low"], g["high"]] for g in relation.get("gaps", [])],
             overlaps=[[o["low"], o["high"]] for o in relation.get("overlaps", [])])
    options = {"gap_fill": "background", "overlap_policy": "split", "overlap_connection_mhz": 60.0}
    result, error = try_call(lambda: bp.combine_frequency(dlr0500, **options))
    combined_case("combine frequency connection", "combine", dlr0500, result, error, options=options)
    result, error = try_call(lambda: bp.combine_time_frequency(dlr0500 + dlr0515))
    combined_case("combine time x frequency", "combine", dlr0500 + dlr0515, result, error, options={})

    # --- processing -----------------------------------------------------
    src = load_callisto_fits(fixture("INDIA-GAURI_20261007_041500_02.fit.gz")).data
    art = load_callisto_fits(str(FIXTURES / "ARTEMIS_00714.FITS")).data
    for label, data in (("gauri", src), ("artemis", art)):
        case(f"input {label}", "input", data=save(f"input.{label}", data))
        for method in ("mean", "median", "robust", "plotutil_median_db"):
            for equalize in (False, True):
                out = subtract_background(data, method, equalize_noise=equalize, db_scale=PLOTUTIL_DB_SCALE)
                case(
                    f"background {label} {method} {equalize}",
                    "background",
                    input=label,
                    method=method,
                    equalize=equalize,
                    db_scale=PLOTUTIL_DB_SCALE,
                    out=save(f"bg.{label}.{method}.{equalize}", out),
                )
        q = row_quantiles(np.asarray(data, dtype=np.float32), (5.0, 50.0, 95.0))
        case(f"quantiles {label}", "quantiles", input=label, qs=[5.0, 50.0, 95.0], out=save(f"q.{label}", q))
        case(f"noise scale {label}", "noise_scale", input=label,
             out=save(f"ns.{label}", rowwise_noise_scale(data)[:, 0]))
        for kt, kf in ((3, 3), (5, 3), (1, 7)):
            r = clean_rfi(data, kernel_time=kt, kernel_freq=kf)
            case(f"rfi {label} {kt}x{kf}", "rfi", input=label, kernel_time=kt, kernel_freq=kf,
                 out=save(f"rfi.{label}.{kt}.{kf}", r.data), masked=list(map(int, r.masked_channel_indices)))
        case(f"db {label}", "db", input=label, cold=12.0, scale=float(2500.0 / 256.0 / 25.4),
             out=save(f"db.{label}", convert_digits_to_db(data, 12.0)))
        lim = fa.percentile_data_limits(data, 5.0, 98.0)
        case(f"limits {label}", "limits", input=label, percentile=list(map(float, lim)),
             finite=list(map(float, fa.finite_data_limits(data))))

    # A gap row (all NaN) goes through background subtraction and RFI.
    gapped = np.array(src, dtype=float)
    gapped[17, :] = np.nan
    case("input gapped", "input", data=save("input.gapped", gapped))
    case("background gapped mean True", "background", input="gapped", method="mean", equalize=True,
         db_scale=PLOTUTIL_DB_SCALE, out=save("bg.gapped", subtract_background(gapped, "mean", equalize_noise=True)))

    # --- frequency axis -------------------------------------------------
    for lo, hi in ((20.0, 80.0), (45.0, 870.0), (100.0, 104.0), (1.0, 5000.0), (175.0, 870.0), (10.0, 100.0)):
        labels, minor = fa.log_frequency_ticks_mhz(lo, hi)
        case(f"log ticks {lo}-{hi}", "log_ticks", lo=lo, hi=hi, labels=labels, minor=minor)
    for v in (870.0, 45.5, 45.0, 1.25, 0.012345, 0.0, 123.456, 9.999):
        case(f"format {v}", "format_mhz", value=v, text=fa.format_frequency_mhz(v))
    load = load_callisto_fits(fixture("GERMANY-DLR_20261007_050000_01.fit.gz"))
    edges = fa.frequency_edges(load.freqs, default_step=fa.frequency_step_mhz(load.freqs))
    resampled, log_rows = fa.resample_rows_to_log_frequency(
        load.data, load.freqs, count=load.data.shape[0], bounds=(float(min(edges)), float(max(edges)))
    )
    case("log resample dlr01", "log_resample", file=Path(load_callisto_fits.__name__).name,
         input_file="GERMANY-DLR_20261007_050000_01.fit.gz", bounds=[float(min(edges)), float(max(edges))],
         out=save("logres.dlr01", resampled), log_rows=list(map(float, log_rows)),
         step=fa.frequency_step_mhz(load.freqs), edges=list(map(float, edges)))

    # --- timeline and archive listing ----------------------------------
    names = [fixture(n) for n in ("GERMANY-DLR_20261007_050000_02.fit.gz", "GERMANY-DLR_20261007_050000_01.fit.gz",
                                  "GERMANY-DLR_20261007_051500_01.fit.gz", "GERMANY-DLR_20261007_051500_02.fit.gz")]
    state = callisto_timeline.describe_timeline(names)
    trimmed, shift = callisto_timeline.trimmed_paths(state, "start")
    case("timeline dlr", "timeline", paths=[Path(p).name for p in names], station=state.station,
         focus_codes=list(state.focus_codes), step=state.step_seconds,
         segments=[[Path(p).name for p in s.paths] for s in state.segments],
         next=callisto_timeline.target_timestamp(state, "next").isoformat(),
         previous=callisto_timeline.target_timestamp(state, "previous").isoformat(),
         trimmed_start=[Path(p).name for p in trimmed], shift=shift)
    html = (FIXTURES.parent / "listing_sample.html").read_text(encoding="utf-8")
    links = callisto_archive.extract_fits_links(html)
    case("archive links", "links", html="listing_sample.html", links=links,
         stations=callisto_archive.stations_in_listing(links))

    (OUT / "cases.json").write_text(json.dumps(CASES, indent=1), encoding="utf-8")
    print(f"{len(CASES)} cases written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
