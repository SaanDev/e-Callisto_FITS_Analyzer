"""Write Lite's user guide from the full app's (`src/ui/help/user_guide_content.py`).

The chapters for features Lite does not have (Solar Image Analysis, PFSS, GCS
fitting, STEREO/SWAVES, the solar event context viewers, the geomagnetic
response, the event context strip and figure composer, the SunPy explorer) are
dropped, the rest renumbered, and every sentence that names a removed feature
is cut or reworded. Each edit must apply exactly once: when the full guide
changes, this script stops and says which edit no longer fits, so the Lite
guide is never silently out of step.

Writes ``lite/crates/callisto-lite/assets/user_guide.html``; Lite fills in
``{{VERSION}}`` at run time.

usage: python lite/tools/make_user_guide.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.ui.help import user_guide_content as guide  # noqa: E402

LITE_NAME = "e-CALLISTO FITS Analyzer Lite"
OUT = REPO / "lite" / "crates" / "callisto-lite" / "assets" / "user_guide.html"

# Chapters Lite does not have, by anchor (each runs to the next anchor).
DROPPED = [
    "solar-image-analysis", "pfss", "gcs-fitting", "swaves", "context-viewers", "geomagnetic-response",
    "event-context-strip", "event-figure", "sunpy-explorer",
]

# Chapter numbers after the drops.
RENUMBER = {"16. FITS Downloader": "14. FITS Downloader"}


def once(text: str, old: str, new: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"edit no longer fits the full guide ({n} matches):\n  {old[:160]!r}")
    return text.replace(old, new)


def drop_row(text: str, start: str) -> str:
    """Removes the table row or list item beginning with `start`."""
    i = text.find(start)
    if i < 0 or text.count(start) != 1:
        raise SystemExit(f"row no longer in the full guide: {start!r}")
    end_tag = "</tr>" if start.startswith("<tr>") else "</li>"
    j = text.index(end_tag, i) + len(end_tag)
    if text[j:j + 1] == "\n":
        j += 1
    return text[:i] + text[j:]


def sections(body: str) -> list[tuple[str, str]]:
    """The body cut at each `<a name>` anchor: (anchor, text)."""
    parts = re.split(r'(?=<a name="[^"]+"></a>)', body)
    out = []
    for p in parts:
        m = re.match(r'<a name="([^"]+)"></a>', p)
        out.append((m.group(1) if m else "", p))
    return out


def lite_body() -> str:
    kept = [text for anchor, text in sections(guide._BODY) if anchor not in DROPPED]
    found = {anchor for anchor, _ in sections(guide._BODY)}
    missing = [a for a in DROPPED if a not in found]
    if missing:
        raise SystemExit(f"dropped chapters no longer in the full guide: {missing}")
    b = "".join(kept)

    # 3. File menu.
    b = once(b, """<td>Reopen a saved <code>.efaproj</code> project. Every window the project needs (Solar Image Analysis, GCS fitting) opens and is restored. An old <code>.ecsolar</code> session can be opened here too: it is imported as a new, unsaved project.</td>""",
             """<td>Reopen a saved <code>.efaproj</code> project: the spectrum with its view, processing, annotations and analysis, and the analysis windows it needs. Projects written by the full e-CALLISTO FITS Analyzer open too; the parts Lite does not show (solar images, GCS fits, the geomagnetic response) are kept and written back unchanged when you save.</td>""")
    b = once(b, """<td>Save everything in one file: the radio spectrum with its view, thresholds, units, colormap, styling and analysis, the Solar Image Analysis frames and measurements, and the GCS fits. A project may hold any of these on its own. {kbd} in the Solar Image Analysis or GCS window saves the same project.</td>""".format(kbd=guide._kbd('Ctrl+S')),
             """<td>Save everything in one file: the radio spectrum with its view, thresholds, units, colormap, styling and analysis.</td>""")
    b = once(b, """<td>Save the project to a new file. When it holds solar or GCS images you choose <b>Link data (small)</b>, which refers to the image files on this computer, or <b>Embed data (portable)</b>, which stores them in the project (the dialog shows the size) so it opens anywhere, even offline. The choice is remembered for {kbd}.</td>""".format(kbd=guide._kbd('Ctrl+S')),
             """<td>Save the project to a new file.</td>""")
    i = b.index("<tr><td><b>Generate Project Report</b></td><td>")
    j = b.index("</td></tr>", i)
    b = b[:i] + ("<tr><td><b>Generate Project Report</b></td><td>Build a consolidated PDF with the data source, header, "
                 "processing, analysis and Type II results, the spectra and the fits. Every graph in the report is drawn "
                 "in the OriginPro style, in light mode.") + b[j:]
    b = drop_row(b, "<tr><td><b>Export &#8594; Event Figure…</b></td>")

    # 4. Edit menu.
    b = once(b, """readouts and undo history are cleared, and the analysis, Geomagnetic Response, Event Figure, Solar Image Analysis
and GCS windows are closed. If the Solar Image Analysis or GCS window holds work, you are asked first. Your
preferences and the download cache are kept.</li>""",
             """readouts and undo history are cleared, and the analysis windows are closed. Your preferences and the download
cache are kept.</li>""")

    # 5. Download menu, 6. Solar Events menu.
    b = once(b, "See <a href=\"#fits-downloader\">section 16</a>.", "See <a href=\"#fits-downloader\">section 14</a>.")
    i = b.index("<h2>6. Solar Events menu</h2>")
    j = b.index('<a name="menu-view"></a>')
    b = b[:i] + ("<h2>6. Solar Events menu</h2>\n<p><b>Radio Bursts</b>: the e-CALLISTO and Learmonth downloaders "
                 "(see <a href=\"#fits-downloader\">section 14</a>).</p>\n\n") + b[j:]

    # 7. View, 8. Analysis, 9. Processing.
    b = drop_row(b, "<li><b>Event Context Strip</b>")
    b = drop_row(b, "<li><b>Mode</b>")
    b = drop_row(b, "<li><b>Solar Image Analysis</b>: open the multi-mission imaging workspace")
    b = drop_row(b, "<li><b>GCS CME Fitting...</b>")
    b = drop_row(b, "<li><b>Hardware Acceleration &#8594; Enable</b>")

    # 12. Sidebar.
    b = drop_row(b, "<li><b>Geomagnetic Response</b>: the event the response is for")

    # 14. FITS Downloader (was 16).
    for old, new in RENUMBER.items():
        b = once(b, f"<h2>{old}</h2>", f"<h2>{new}</h2>")

    # Appendix.
    b = once(b, "<td>Save Project (in the main, Solar Image Analysis and GCS windows)</td>", "<td>Save Project</td>")
    b = drop_row(b, "<tr><td>{kbd}</td><td>Cancel a pick (Solar Image Analysis)</td></tr>".format(kbd=guide._kbd('Esc')))
    b = once(b, """<p class="note">The GCS CME Fitting window has its own keys; see <a href="#gcs-fitting">section 15</a>.</p>\n""", "")
    b = once(b, "<td>Radio spectra loaded by the analyzer, and solar images in the imaging workspace. Upper-case suffixes",
             "<td>Radio spectra loaded by the analyzer. Upper-case suffixes")
    b = once(b, "<td>The one project file: radio analysis, Solar Image Analysis and GCS fitting, with the solar and GCS images linked or embedded. Projects saved by v3.2 cannot be opened by v3.1.</td>",
             "<td>The project file, shared with the full e-CALLISTO FITS Analyzer: Lite opens its projects and keeps the parts it does not show.</td>")
    b = drop_row(b, "<tr><td><code>.ecsolar</code></td>")
    b = drop_row(b, "<li><b>GCS panel says it has no data?</b>")

    # Footer.
    b = once(b, f"<p class=\"muted\">{guide.APP_NAME} version {guide.APP_VERSION}. Developed by",
             f"<p class=\"muted\">{LITE_NAME} version {{{{VERSION}}}}. Developed by")
    return b


def lite_toc() -> str:
    t = guide._TOC
    for anchor in DROPPED:
        for line in [l for l in t.split("\n") if f'href="#{anchor}"' in l]:
            t = once(t, line + "\n", "")
    t = once(t, '<a href="#fits-downloader">16. FITS Downloader</a>', '<a href="#fits-downloader">14. FITS Downloader</a>')
    # The companion windows' paragraph now ends with the downloader (its closing line was the SunPy explorer's).
    t = once(t, '14. FITS Downloader</a><br>\n<p><b>Appendix', '14. FITS Downloader</a></p>\n<p><b>Appendix')
    return t


def main() -> int:
    head = f"""
<a name="top"></a>
<h1>{LITE_NAME}</h1>
<p class="lead">User Guide &#8226; version {{{{VERSION}}}}</p>
<p>This guide explains how to load and view e-CALLISTO solar radio spectra, reduce noise, isolate and analyze
bursts, and use the built-in downloaders. Start with the Quick Start, then use the reference sections for details.
Click any entry below to jump to it.</p>
"""
    full_head = guide.USER_GUIDE_HTML.split(guide._TOC)[0]
    if "and use the built-in downloaders and solar-event tools." not in full_head.replace("\n", " "):
        raise SystemExit("the guide's introduction changed: update the Lite introduction")
    html = head + lite_toc() + "<hr>\n" + lite_body()
    for anchor in DROPPED:
        if f'href="#{anchor}"' in html:
            raise SystemExit(f"a link to a dropped chapter is left: #{anchor}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html.lstrip("\n"), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)} ({len(html)} characters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
