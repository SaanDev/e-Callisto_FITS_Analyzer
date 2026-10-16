"""Write the full app's results for the downloader and data-import code.

Like ``make_reference.py``: runs the full app's own functions on the parity
fixtures and on synthetic inputs, and writes ``lite/parity/out/downloads.json``
(plus ``.npy`` arrays and a ``learmonth`` folder) for the Rust test
``crates/callisto-core/tests/downloads_parity.rs``.

Nothing is downloaded: the cases use local files, synthetic catalog tables and
a synthetic Learmonth day. The full app's download cache is not touched.

usage (from the repository root, in the full app's environment):
    python lite/parity/make_download_reference.py
"""

from __future__ import annotations

import json
import struct
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from src.backend.radio import burst_list as bl  # noqa: E402
from src.backend.radio import callisto_archive, callisto_cache  # noqa: E402
from src.backend.radio import learmonth as lm  # noqa: E402
from src.backend.radio import spectral_overview as so  # noqa: E402

# Keep the full app's real cache out of this: nothing here may evict from it.
callisto_cache.enforce_cache_limit = lambda *args, **kwargs: 0

from src.ui.app import gui_workers  # noqa: E402
from src.ui.downloads import callisto_downloader as cd  # noqa: E402
from src.ui.radio import burst_fits as bf  # noqa: E402

FIXTURES = Path(__file__).with_name("fixtures")
OUT = Path(__file__).with_name("out")
LM_DIR = OUT / "learmonth"
CASES: list[dict] = []


def save(name: str, arr) -> str:
    fname = f"dl.{name}.npy"
    np.save(OUT / fname, np.ascontiguousarray(np.asarray(arr, dtype=np.float64)))
    return fname


def case(name: str, kind: str, **fields) -> None:
    CASES.append({"name": name, "kind": kind, **fields})


def fixture(name: str) -> str:
    return str(FIXTURES / name)


def iso(value):
    return value.isoformat(sep=" ") if value is not None else None


def candidate(c) -> dict:
    return {"station": c.station, "observed_at": iso(c.observed_at_utc), "filename": c.filename, "url": c.url,
            "receiver_id": c.receiver_id}


def event(e) -> dict:
    return {"start": iso(e.start_utc), "end": iso(e.end_utc), "type": e.burst_type,
            "fmin": e.frequency_min_mhz, "fmax": e.frequency_max_mhz, "stations": list(e.stations),
            "remarks": e.remarks, "source_url": e.source_url, "raw_line": e.raw_line}


# --------------------------------------------------------------- Learmonth

def write_learmonth_day(path: Path, start: datetime, scans: int, seed: int, step_byte: int = 3) -> None:
    rng = np.random.default_rng(seed)
    buf = bytearray()
    for i in range(scans):
        t = start + timedelta(seconds=3 * i)
        buf += bytes([t.year - 2000, t.month, t.day, t.hour, t.minute, t.second, step_byte, 0])
        buf += struct.pack(">HH4s", 25, 75, b"\0\0\0\0") + struct.pack(">HH4s", 75, 180, b"\0\0\0\0")
        buf += rng.integers(0, 256, 802, dtype=np.uint8).tobytes()
    path.write_bytes(bytes(buf))


def learmonth_cases() -> None:
    LM_DIR.mkdir(parents=True, exist_ok=True)
    days = [("LM240501.srs", datetime(2024, 5, 1, 23, 50, 0), 320, 1, 3),
            ("LM240502.srs", datetime(2024, 5, 2, 6, 0, 0), 600, 2, 0)]
    for name, start, scans, seed, step in days:
        src = LM_DIR / name
        write_learmonth_day(src, start, scans, seed, step)
        chunks = lm.list_learmonth_chunks(src)
        outputs = []
        for chunk in chunks:
            out = LM_DIR / f"{name}.{chunk.index}.fit"
            lm.write_learmonth_chunk_fit(src, chunk, out)
            outputs.append(out.name)
        case(f"learmonth {name}", "learmonth", srs=f"learmonth/{name}",
             chunks=[{"index": c.index, "start": iso(c.start_dt), "end": iso(c.end_dt), "scans": c.scan_count,
                      "offset_start": c.offset_start, "offset_end": c.offset_end, "partial": c.is_partial}
                     for c in chunks],
             fits=[f"learmonth/{o}" for o in outputs],
             fit_names=[lm.learmonth_fit_filename(c) for c in chunks],
             url=lm.resolve_learmonth_url(start.date()))


# --------------------------------------------------------------- burst list

CATALOGS = {
    "https://x/BurstLists/2010-yyyy_Monstein/2023/e-CALLISTO_2023_01.txt": (
        "\ufeff# e-CALLISTO burst list January 2023\r\n"
        "#Date\tTime\tType\tStations\r\n"
        "20230105\t10:15-10:17\tIII\tBIR, (MRO), ALMATY?\tweak\r\n"
        "20230105\t10:15-10:17\tIII\tBIR, (MRO), ALMATY?\tweak\r\n"
        "20230105\t11:00-11:05\tREM\tno data from 11 to 12\r\n"
        "20230105 23:59-00:02 II  GLASGOW # drifting\r\n"
        "20230106\t25:00-25:10\tIII\tBIR\r\n"
        "20230106\t08:03:30-08:04:10\tIIIG+V\tAUSTRIA-UNIGRAZ; Australia-ASSA [?]\r\n"
        "20230107   ---\r\n"
        "20230108\t09:00\u201309:12\tIV\tINDIA-OOTY,INDIA-OOTY,india-ooty\r\n"
        "2023010 broken line\r\n"
        "20230109\t24:00-24:00\tCTM\t---\r\n"
        "20230131\t10:00-10:01\tU\tSWISS-Landschlacht\r\n"
        "20230201\t10:00-10:01\tJ\tHUMAIN\r\n"
        "free text without date\n"
    ),
    "https://x/BurstLists/2010-yyyy_Monstein/2014/20141101-20141130_nhit2.txt": (
        "Product: CELESTINA coincidences\n"
        "#Date Time Stations\n"
        "20141102 10:00-10:01 BIR, MRO\n"
        "20141103 10:00:05-10:00:59 ALMATY\n"
    ),
    "https://x/BurstLists/2010-yyyy_Monstein/2011/SGD_BLEN_2011_06.txt": (
        "Some legend line\n"
        "110607 0700 1600 BLEN 0815 0815.5 IIIG 2 45 870X\n"
        "110607 BLEN 1203.2 1210 II/IV 3 <45 >870\n"
        "110608 BLEN\n"
        "110609 BLEN 2359 0001 III 1 100 200\n"
        "110610 BLEN 1260 1261 III 1 100 200\n"
        "11061 BLEN short\n"
    ),
    "https://x/BurstLists/2010-yyyy_Monstein/2020/e-CALLISTO_2020_03.txt": (
        "No bursts observed this month.\n"
    ),
}

LISTING = (
    '<html><body><a href="?C=N;O=D">Name</a> <a href="/BurstLists/">Parent</a>'
    ' <a href="2023/">2023/</a> <A HREF=\'2024/\'>2024/</A> <a href="2023/">dup</a>'
    ' <a href="e-CALLISTO_2023_01.txt#frag">x</a> <a href="https://other/2023/">y</a>'
    ' <a href="20141101-20141130_nhit3.txt">z</a> <a href="e-CALLISTO_2023_13.txt">bad</a>'
    ' <a href="a&amp;b.txt">amp</a></body></html>'
)


def burst_cases() -> None:
    all_events = []
    for url, text in CATALOGS.items():
        parsed = bl._parse_content(text, source_url=url)
        case(f"catalog {url.rsplit('/', 1)[-1]}", "catalog", url=url, text=text,
             events=[event(e) for e in parsed.events], notes=parsed.notes, invalid=parsed.invalid,
             notices=[[d.isoformat(), n] for d, n in parsed.notices], recognized=parsed.recognized)
        all_events.extend(parsed.events)
    filters = [("", "", ""), ("III", "", ""), ("II", "", ""), ("IV", "", ""), ("V", "", ""),
               ("unknown", "", ""), ("II/IV", "", ""), ("", "bir", ""), ("", "", "drift"), ("", "", "ooty"),
               ("III", "mro", "")]
    for burst_type, station, text in filters:
        out = bl.filter_burst_events(all_events, burst_type=burst_type, station=station, text=text)
        case(f"filter {burst_type!r} {station!r} {text!r}", "burst_filter", burst_type=burst_type,
             station=station, text=text, raw_lines=[e.raw_line for e in out])
    for lo, hi in ((100.0, None), (None, 50.0), (40.0, 60.0)):
        out = bl.filter_burst_events(all_events, frequency_min_mhz=lo, frequency_max_mhz=hi)
        case(f"filter frequency {lo} {hi}", "burst_filter", burst_type="", station="", text="", fmin=lo, fmax=hi,
             raw_lines=[e.raw_line for e in out])
    directory = "https://x/BurstLists/2010-yyyy_Monstein/"
    case("listing links", "burst_links", html=LISTING, directory=directory,
         links=bl._listing_links(LISTING, directory))
    names = ["e-CALLISTO_2024_02.txt", "SGD_BLEN_2011_06.TXT", "20141101-20141130_nhit2.txt", "readme.txt",
             "e-CALLISTO_2023_13.txt", "e-callisto_2023_1.txt", "20141301-20141330_nhit2.txt"]
    periods = {}
    for name in names:
        p = bl._source_period(directory + name)
        periods[name] = None if p is None else [p[0].isoformat(), p[1].isoformat(), p[2]]
    case("source periods", "source_periods", directory=directory, periods=periods)


# --------------------------------------------------------------- candidates

def candidate_cases() -> None:
    html = (FIXTURES.parent / "listing_sample.html").read_text(encoding="utf-8")
    hrefs = callisto_archive.extract_fits_links(html)
    day = "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto/2026/10/07/"
    windows = [("2026-10-07 00:00:00", "2026-10-07 10:20:00", ["BIR", "malaysia_banting", "Glasgow", "nope"]),
               ("2026-10-07 10:14:59", "2026-10-07 10:30:00", ["bir"]),
               ("2026-10-07 00:00:00", "2026-10-07 23:59:59", ["ALASKA-ANCHORAGE", "MRO"])]
    for start, stop, stations in windows:
        s, e = datetime.fromisoformat(start), datetime.fromisoformat(stop)
        found = cd.filter_event_candidates(hrefs, day_url=day, selected_stations=stations, start_dt=s, stop_dt=e)
        case(f"event {start} {stations}", "event_filter", hrefs=hrefs, day_url=day, stations=stations,
             start=start, stop=stop, candidates=[candidate(c) for c in cd.sort_event_candidates(found)])
    for station in ("BIR", "bir", "Malaysia_Banting", "GLASGOW"):
        found = cd.filter_spectral_overview_candidates(hrefs, day_url=day, station=station,
                                                      observation_date=date(2026, 10, 7))
        groups = cd.group_spectral_overview_candidates(found)
        case(f"overview candidates {station}", "overview_filter", hrefs=hrefs, day_url=day, station=station,
             date="2026-10-07", candidates=[candidate(c) for c in found],
             groups={k: [c.filename for c in v] for k, v in groups.items()},
             labels={k: cd.CallistoDownloaderApp._focus_group_label(k, v) for k, v in groups.items()})
    for start, stop, stations in (("2026-10-07 10:20:00", "2026-10-07 10:21:00", ["BIR"]),
                                  ("2026-10-07 00:05:00", "2026-10-07 00:05:00", ["glasgow", "uk-glasgow"]),
                                  ("2026-10-06 23:50:00", "2026-10-07 00:01:00", ["Malaysia-Banting"])):
        s, e = datetime.fromisoformat(start), datetime.fromisoformat(stop)
        found = bf.filter_burst_candidates(hrefs, day_url=day, selected_stations=stations, start_dt=s, stop_dt=e)
        case(f"burst fits {start} {stations}", "burst_fits_filter", hrefs=hrefs, day_url=day, stations=stations,
             start=start, stop=stop, candidates=[candidate(c) for c in found],
             dates=[d.isoformat() for d in bf.burst_archive_dates(s, e)])


# --------------------------------------------------------------- overview, previews, import

def overview_cases() -> None:
    sets = {
        "gauri": ["INDIA-GAURI_20261007_041500_02.fit.gz", "INDIA-GAURI_20261007_043000_02.fit.gz",
                  "INDIA-GAURI_20261007_050000_02.fit.gz"],
        "dlr01": ["GERMANY-DLR_20261007_051500_01.fit.gz", "GERMANY-DLR_20261007_050000_01.fit.gz"],
    }
    for label, names in sets.items():
        sources = []
        for name in names:
            station, observed, focus = cd.parse_callisto_archive_filename(name)
            sources.append(so.SpectralOverviewSource(path=fixture(name), station=station, observed_at_utc=observed,
                                                     focus_code=focus, filename=name))
        result = so.build_spectral_overview(sources)
        segs = []
        for i, seg in enumerate(result.segments):
            segs.append({"filename": seg.source.filename, "group": seg.frequency_group,
                         "data": save(f"overview.{label}.{i}", seg.data_db), "freqs": list(map(float, seg.freqs)),
                         "utc": save(f"overview.{label}.{i}.utc", seg.utc_seconds)})
        case(f"overview {label}", "overview", files=names, segments=segs, total=result.total_sources,
             loaded=result.loaded_sources, coverage=result.coverage_seconds, warnings=list(result.warnings),
             station=result.station, date=result.observation_date.isoformat(), focus=result.focus_code)


def preview_cases() -> None:
    sets = {
        "single": ["BIR_20261007_101459_01.fit.gz"],
        "gauri time": ["INDIA-GAURI_20261007_041500_02.fit.gz", "INDIA-GAURI_20261007_043000_02.fit.gz"],
        "mixed": ["INDIA-GAURI_20261007_041500_02.fit.gz", "BIR_20261007_101459_01.fit.gz",
                  "INDIA-GAURI_20261007_043000_02.fit.gz", "TRIEST_20261007_050000_57.fit.gz",
                  "TRIEST_20261007_050000_59.fit.gz"],
        "dlr grid": ["GERMANY-DLR_20261007_050000_01.fit.gz", "GERMANY-DLR_20261007_050000_02.fit.gz",
                     "GERMANY-DLR_20261007_051500_01.fit.gz", "GERMANY-DLR_20261007_051500_02.fit.gz"],
    }
    for label, names in sets.items():
        panels, errors = cd.build_preview_panels([(fixture(n), n) for n in names])
        out = []
        for i, p in enumerate(panels):
            out.append({"title": p["title"], "subtitle": p["subtitle"], "combine_type": p["combine_type"],
                        "data": save(f"preview.{label}.{i}", p["data"]), "freqs": list(map(float, p["freqs"])),
                        "time": list(map(float, p["time"]))})
        case(f"preview {label}", "preview", files=names, panels=out, errors=errors)


def import_cases() -> None:
    sets = {
        "single": ["BIR_20261007_101459_01.fit.gz"],
        "time": ["INDIA-GAURI_20261007_041500_02.fit.gz", "INDIA-GAURI_20261007_043000_02.fit.gz"],
        "frequency": ["GERMANY-DLR_20261007_050000_01.fit.gz", "GERMANY-DLR_20261007_050000_02.fit.gz",
                      "GERMANY-DLR_20261007_050000_03.fit.gz"],
        "triest": ["TRIEST_20261007_050000_57.fit.gz", "TRIEST_20261007_050000_59.fit.gz"],
        "invalid": ["BIR_20261007_101459_01.fit.gz", "MEXART_20261007_001500_59.fit.gz"],
        "missing": ["C:/definitely/missing/file.fit"],
    }
    for label, names in sets.items():
        sources = [n if "/" in n else fixture(n) for n in names]
        worker = gui_workers.DownloaderImportWorker(sources)
        events: list = []
        worker.progress_text.connect(lambda t: events.append(["text", t]))
        worker.progress_range.connect(lambda a, b: events.append(["range", a, b]))
        worker.progress_value.connect(lambda v: events.append(["value", v]))
        result: dict = {}
        worker.finished.connect(lambda p: result.update(payload=p))
        worker.failed.connect(lambda m: result.update(error=m))
        worker.run()
        entry = {"files": names, "progress": events}
        if "error" in result:
            entry["error"] = result["error"]
        else:
            p = result["payload"]
            entry["payload_kind"] = p["kind"]
            if p["kind"] == "single":
                entry["filename"] = p["filename"]
                entry["shape"] = list(np.asarray(p["data"]).shape)
            elif p["kind"] == "combined":
                entry["shape"] = list(np.asarray(p["combined"]["data"]).shape)
                entry["combine_type"] = p["combined"]["combine_type"]
            elif p["kind"] == "frequency_options_required":
                entry["combine_type"] = p["combine_type"]
                entry["has_gap"] = bool(p["relation"].get("has_gap"))
                entry["has_overlap"] = bool(p["relation"].get("has_overlap"))
            else:
                entry["invalid_error"] = p.get("error", "")
        case(f"import {label}", "import", **entry)


def main() -> int:
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
    OUT.mkdir(parents=True, exist_ok=True)
    learmonth_cases()
    burst_cases()
    candidate_cases()
    overview_cases()
    preview_cases()
    import_cases()
    (OUT / "downloads.json").write_text(json.dumps(CASES, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(CASES)} cases written to {OUT / 'downloads.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
