"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

In-app user guide content.

This module holds the user guide as a self-contained HTML string that is
rendered by ``QTextBrowser`` (see ``src/UI/dialogs/user_guide_dialog.py``).
Keeping the guide as a normal Python module means it is packaged automatically
by PyInstaller with the rest of the code, so no ``datas`` spec entry and no
runtime path resolution are needed.

The HTML uses only the rich-text subset that ``QTextBrowser`` supports
(headings, paragraphs, lists, tables, bold/italic, ``<a name>`` anchors, and
``<a href="#anchor">`` internal links). Colors are supplied separately through
``build_default_stylesheet`` so the document matches the app light/dark theme.
"""

from __future__ import annotations

from src.version import APP_NAME, APP_VERSION


# ---------------------------------------------------------------------------
# Theme-aware document stylesheet
# ---------------------------------------------------------------------------

# Palette values mirror src/UI/theme_manager.py so the guide reads like the
# rest of the application in both modes.
_DARK_COLORS = {
    "text": "#e8eef8",
    "muted": "#9db0c9",
    "heading": "#ffffff",
    "accent": "#4ea3ff",
    "border": "#314055",
    "surface": "#171f2b",
    "code": "#c9e2ff",
}

_LIGHT_COLORS = {
    "text": "#202a36",
    "muted": "#61758f",
    "heading": "#0f2338",
    "accent": "#146fda",
    "border": "#d3dcea",
    "surface": "#eef3fb",
    "code": "#0b4da2",
}


def build_default_stylesheet(dark: bool) -> str:
    """Return document CSS for the guide, matching the active theme.

    Applied via ``QTextBrowser.document().setDefaultStyleSheet(...)`` before the
    HTML is set. ``QTextBrowser`` supports a limited CSS subset, so this keeps to
    color, spacing, and simple table styling.
    """

    c = _DARK_COLORS if dark else _LIGHT_COLORS
    return f"""
    body {{ color: {c['text']}; font-size: 10.5pt; line-height: 140%; }}
    h1 {{ color: {c['heading']}; font-size: 20pt; }}
    h2 {{ color: {c['heading']}; font-size: 15pt; border-bottom: 1px solid {c['border']}; padding-bottom: 3px; }}
    h3 {{ color: {c['heading']}; font-size: 12.5pt; }}
    h4 {{ color: {c['accent']}; font-size: 11pt; }}
    p, li {{ color: {c['text']}; }}
    a {{ color: {c['accent']}; text-decoration: none; }}
    .muted {{ color: {c['muted']}; }}
    .lead {{ color: {c['muted']}; font-size: 11pt; }}
    code, .kbd {{ color: {c['code']}; font-family: 'Consolas','Courier New',monospace; }}
    .toc a {{ color: {c['accent']}; }}
    table {{ border-collapse: collapse; }}
    th {{ color: {c['heading']}; text-align: left; border-bottom: 1px solid {c['border']}; padding: 4px 12px 4px 0; }}
    td {{ color: {c['text']}; border-bottom: 1px solid {c['border']}; padding: 4px 12px 4px 0; }}
    .note {{ color: {c['muted']}; }}
    hr {{ color: {c['border']}; }}
    """


# ---------------------------------------------------------------------------
# Guide body
# ---------------------------------------------------------------------------

def _kbd(text: str) -> str:
    return f'<code>{text}</code>'


_TOC = """
<div class="toc">
<p><b>Contents</b></p>
<p><b>Getting started</b><br>
&#8226; <a href="#quick-start">1. Quick Start</a><br>
&#8226; <a href="#interface">2. The Main Window</a></p>
<p><b>Menu reference</b><br>
&#8226; <a href="#menu-file">3. File menu</a><br>
&#8226; <a href="#menu-edit">4. Edit menu</a><br>
&#8226; <a href="#menu-download">5. Download menu</a><br>
&#8226; <a href="#menu-solar-events">6. Solar Events menu</a><br>
&#8226; <a href="#menu-view">7. View menu</a><br>
&#8226; <a href="#menu-analysis">8. Analysis menu</a><br>
&#8226; <a href="#menu-processing">9. Processing menu</a><br>
&#8226; <a href="#menu-about">10. About menu</a></p>
<p><b>Tools and controls</b><br>
&#8226; <a href="#toolbar">11. Toolbar</a><br>
&#8226; <a href="#sidebar">12. Left sidebar panels</a><br>
&#8226; <a href="#navigation">13. Navigation and picking</a></p>
<p><b>Companion windows</b><br>
&#8226; <a href="#solar-image-analysis">14. Solar Image Analysis</a><br>
&#8226; <a href="#fits-downloader">15. FITS Downloader</a><br>
&#8226; <a href="#context-viewers">16. Solar event context viewers</a><br>
&#8226; <a href="#sunpy-explorer">17. SunPy Multi-Mission Explorer</a></p>
<p><b>Appendix</b><br>
&#8226; <a href="#shortcuts">A. Keyboard shortcuts</a><br>
&#8226; <a href="#file-types">B. File types</a><br>
&#8226; <a href="#tips">C. Tips and troubleshooting</a></p>
</div>
"""


_BODY = f"""
<a name="quick-start"></a>
<h2>1. Quick Start</h2>
<p class="lead">The fastest path from a raw file to an analysis figure.</p>
<ol>
<li><b>Open a file.</b> Choose <code>File &#8594; Open</code> (or press {_kbd('Ctrl+O')}, or click the
Open icon on the toolbar) and select an e-CALLISTO FITS file. The dynamic spectrum appears at once.</li>
<li><b>Clean up the display.</b> Drag the <b>Lower Threshold</b> and <b>Upper Threshold</b> sliders in the
left sidebar. The plot updates live, so you can watch the burst emerge from the background.</li>
<li><b>Pick units and colors.</b> In <b>Units</b> choose Digits or dB, and Seconds or UT. In
<b>Graph Properties</b> pick a colormap.</li>
<li><b>Focus on the burst.</b> Click <b>Isolate Burst</b> on the toolbar and draw a loop around the emission.
Only the enclosed region is kept for analysis.</li>
<li><b>Extract the ridge.</b> Open <code>Analysis &#8594; Maximum Intensities</code> to trace the peak
frequency over time, then lasso-select and remove any stray outliers.</li>
<li><b>Fit and measure.</b> Run the Burst Analyzer for a power-law fit, drift rate, and shock speed/height,
or use <code>Analysis &#8594; Type II Band-splitting</code> for a magnetic-field estimate.</li>
<li><b>Export.</b> Save the figure with <code>File &#8594; Export As &#8594; Export Figure</code>
(or {_kbd('Ctrl+E')}), and save your whole session with <code>File &#8594; Save Project</code>
({_kbd('Ctrl+S')}).</li>
</ol>
<p class="note">Tip: many buttons stay greyed out until a file is loaded. Open a file first and the
analysis controls become active.</p>

<a name="interface"></a>
<h2>2. The Main Window</h2>
<p>The window has four regions:</p>
<ul>
<li><b>Menu bar</b> (top): File, Edit, Download, Solar Events, View, Analysis, Processing, About, and Help.</li>
<li><b>Toolbar</b> (below the menu): icon buttons for the most common actions, plus a
<b>Cite this Software</b> button on the right.</li>
<li><b>Left sidebar</b>: grouped controls for thresholds, units, graph appearance, the analysis summary,
and ruler readouts. A vertical arrow button on its edge hides or shows the sidebar to give the plot more room.</li>
<li><b>Viewer</b> (center): the dynamic spectrum. A status bar at the bottom shows messages on the left and,
on the right, the live cursor readout (time, frequency, intensity) and the update-check status.</li>
</ul>

<a name="menu-file"></a>
<h2>3. File menu</h2>
<table>
<tr><th>Item</th><th>What it does</th></tr>
<tr><td><b>Open</b> ({_kbd('Ctrl+O')})</td><td>Load a FITS file into the analyzer.</td></tr>
<tr><td><b>Open Project</b> ({_kbd('Ctrl+Shift+O')})</td><td>Reopen a saved <code>.efaproj</code> session.</td></tr>
<tr><td><b>Save Project</b> ({_kbd('Ctrl+S')})</td><td>Save the full state: view, thresholds, units, colormap, styling, data, and analysis session.</td></tr>
<tr><td><b>Save Project As</b> ({_kbd('Ctrl+Shift+S')})</td><td>Save the project to a new file.</td></tr>
<tr><td><b>Recover Last Session</b></td><td>Restore the most recent autosave snapshot after a crash.</td></tr>
<tr><td><b>Generate Project Report</b></td><td>Build a consolidated PDF with the spectra, fits, and available solar-context plots.</td></tr>
<tr><td><b>Export &#8594; Export Figure</b> ({_kbd('Ctrl+E')})</td><td>Save the current plot as PNG, PDF, EPS, SVG, or TIFF.</td></tr>
<tr><td><b>Export &#8594; Export to FIT</b> ({_kbd('Ctrl+F')})</td><td>Write processed data (raw, background-subtracted, or combined) as a new FITS file.</td></tr>
<tr><td><b>Export &#8594; Export Provenance Report</b></td><td>Write Markdown and JSON summaries of the source, processing, RFI, annotations, and operation log.</td></tr>
<tr><td><b>Export &#8594; Export Analysis Log</b></td><td>Write CSV and text summaries of fit parameters and derived shock metrics.</td></tr>
</table>

<a name="menu-edit"></a>
<h2>4. Edit menu</h2>
<ul>
<li><b>Undo</b> ({_kbd('Ctrl+Z')}) and <b>Redo</b> ({_kbd('Ctrl+Shift+Z')}): step through processing changes.</li>
<li><b>Reset to Raw</b>: revert all applied processing and return to the original loaded data.</li>
<li><b>Reset All</b>: clear processing, selections, and analysis overlays.</li>
</ul>

<a name="menu-download"></a>
<h2>5. Download menu</h2>
<p><b>Launch FITS Downloader</b> opens the e-CALLISTO downloader window (also reachable from
<code>Solar Events &#8594; Radio Bursts</code>). See <a href="#fits-downloader">section 15</a>.</p>

<a name="menu-solar-events"></a>
<h2>6. Solar Events menu</h2>
<p>Opens external solar and geophysical data tools, grouped in submenus:</p>
<ul>
<li><b>CMEs &#8594; SOHO/LASCO CME Catalog</b>: daily CME lists, parameter table, and LASCO movies.</li>
<li><b>Flares &#8594; GOES X-Ray Flux</b>: the standalone X-ray viewer.</li>
<li><b>Energetic Particles &#8594; GOES SEP Proton Flux</b>: NOAA SGPS proton flux across a date range.</li>
<li><b>Geomagnetic</b>: Kyoto Dst Index and GFZ Kp Index viewers.</li>
<li><b>Archives &#8594; SunPy Multi-Mission Explorer</b>: search and plot external imagery and time series
(see <a href="#sunpy-explorer">section 17</a>).</li>
<li><b>Radio Bursts</b>: the e-CALLISTO and Learmonth downloaders.</li>
<li><b>Sync Current Time Window</b>: push the analyzer time window to supported context viewers.</li>
<li><b>GOES Overlay</b>: draw the GOES long channel (XRS-B) and/or short channel (XRS-A) directly on the
spectrum, with a flare-class guide (A/B/C/M/X). The overlay does not alter the data.</li>
</ul>

<a name="menu-view"></a>
<h2>7. View menu</h2>
<ul>
<li><b>View FITS Header</b>: inspect the header of the loaded file.</li>
<li><b>Set Display Range</b>: type exact start/stop times and frequency bounds for aligned comparisons.</li>
<li><b>Save / Apply / Delete Display Range Preset</b>: reuse the same visible window on later files.</li>
<li><b>Export / Import View Config</b>: share the display range, units, thresholds, colormap, and styling as an
<code>.efaview.json</code> file.</li>
<li><b>Multi-Station Comparison</b>: stack several station spectra in synchronized panels aligned by UT clock or
seconds from file start, with shared, per-station, or manual color scaling, then export the view.</li>
<li><b>Theme</b>: System, Light, or Dark.</li>
<li><b>Mode</b>: Classic (Matplotlib rendering) or Modern (hardware-accelerated rendering).</li>
</ul>

<a name="menu-analysis"></a>
<h2>8. Analysis menu</h2>
<ul>
<li><b>Solar Image Analysis</b>: open the multi-mission imaging workspace
(see <a href="#solar-image-analysis">section 14</a>).</li>
<li><b>Maximum Intensities &#8594; Open Maximum Intensities</b>: trace the peak frequency for each time channel
after noise reduction or burst isolation. Inside that window you can lasso-select outliers and remove them.</li>
<li><b>Type II Band-splitting &#8594; Open Type II Band-splitting</b>: pick points along the upper and lower
bands, fit both, and derive shock speed, height, bandwidth, compression ratio, Alfven Mach number, Alfven speed,
and magnetic field. This workflow is <b>experimental</b>; validate results against known events.</li>
<li><b>Plot Light Curves</b>: overlay intensity-versus-time curves. Enter a frequency, or switch on
<b>Click on a frequency</b> and click the spectrum. Use <b>Settings</b> for color, width, opacity, scale, line
style, and labels; <b>Clear light curve(s)</b> removes them without resetting the data.</li>
<li><b>Ruler Measurement</b>: click two points on the spectrum to read duration, frequency change, and drift
slope; <b>Clear Ruler Measurement</b> removes it.</li>
</ul>

<a name="menu-processing"></a>
<h2>9. Processing menu</h2>
<ul>
<li><b>Hardware Acceleration &#8594; Enable</b>: toggle the accelerated renderer.</li>
<li><b>RFI Cleaning</b>: <b>Open RFI Panel</b>, <b>Apply RFI</b>, and <b>Reset RFI</b>. The panel runs a
deterministic pipeline (median smoothing, hot-channel masking, masked-channel repair, and per-channel upper
percentile clipping). Sensible defaults are kernel 3 by 3, Channel Z threshold 6.0, and percentile clip 99.5.
Use <b>Preview</b> to inspect, then <b>Apply</b>. If channel streaks remain, lower the Channel Z threshold
gradually; if detail looks over-smoothed, reduce the kernels.</li>
<li><b>Annotations</b>: add Polygon, Line, or Text; edit or move a text label; toggle visibility; delete the last;
or clear all. Annotations are saved with the project.</li>
<li><b>Presets</b>: apply the Raw FITS Percentile (5 to 98%) preset for a quick starting range, save the current
settings as a preset, apply or delete a preset, and set or clear a default preset applied to future loads.</li>
<li><b>Maximum Intensity &#8594; Auto-Clean Isolated Burst Outliers</b>: automatically drop outliers while still
allowing manual cleanup.</li>
<li><b>Analysis Session &#8594; Open Restored Analysis</b>: reopen a saved analysis session.</li>
<li><b>Batch Processing &#8594; Open Batch Processor</b>: export many files from a folder with consistent
background subtraction (per-channel mean, median, or median_dB) and, optionally, a locked display range or saved
view config.</li>
</ul>

<a name="menu-about"></a>
<h2>10. About menu</h2>
<ul>
<li><b>Check for Updates</b>: query GitHub for a newer release and download it in-app.</li>
<li><b>Report a Bug</b>: capture session and environment details, build a diagnostics ZIP, and open a prefilled
GitHub issue draft.</li>
<li><b>About</b>: version and author information.</li>
</ul>
<p class="note">The <b>Help &#8594; User Guide</b> menu (shortcut {_kbd('F1')}) opens this document.</p>

<a name="toolbar"></a>
<h2>11. Toolbar</h2>
<p>Icon buttons, left to right, cover the common actions: Open ({_kbd('Ctrl+O')}), Download, Export
({_kbd('Ctrl+E')}), Export as FITS ({_kbd('Ctrl+F')}), Save Project, Undo, Redo, Estimate Drift Rate,
Isolate Burst, Plot Maximum Intensities, Rectangular Zooming, Lock/Unlock navigation, Reset Selection,
Reset to Raw, and Reset All. On the right is the <b>Cite this Software</b> button. Actions that need processed
data or a loaded file stay disabled until they are usable.</p>

<a name="sidebar"></a>
<h2>12. Left sidebar panels</h2>
<ul>
<li><b>Noise Clipping Thresholds</b>: the <b>Lower</b> and <b>Upper</b> sliders set the color-scale limits
(Vmin/Vmax) live. The <b>Logarithmic Threshold Scale</b> checkbox gives finer control near zero.</li>
<li><b>Units</b>: Intensity as Digits or dB, and Time as Seconds or UT. Switching time units does not lose data.</li>
<li><b>Graph Properties</b> (active after a file loads): colormap (Custom, viridis, plasma, inferno, magma,
cividis, turbo, RdYlBu, jet, cubehelix, bone_r), font family, graph title and a Remove Titles checkbox, font
sizes for tick labels/axis labels/title, and Bold/Italic style toggles.</li>
<li><b>Analysis Summary</b>: a read-only summary of the current analysis session.</li>
<li><b>Ruler Measurement</b>: the latest ruler readout, with a Clear control.</li>
</ul>

<a name="navigation"></a>
<h2>13. Navigation and picking</h2>
<table>
<tr><th>Interaction</th><th>How</th></tr>
<tr><td>Zoom</td><td>Scroll the mouse wheel over the plot (centered on the cursor).</td></tr>
<tr><td>Pan</td><td>Hold the left mouse button and drag inside the plot.</td></tr>
<tr><td>Rectangle zoom</td><td>Click <b>Lock</b> on the toolbar first, then drag a rectangle.</td></tr>
<tr><td>Cursor readout</td><td>Move the cursor over the plot to read time, frequency, and intensity in the status bar.</td></tr>
<tr><td>Estimate drift rate</td><td>Click points along the burst; right-click or double-click to finish.</td></tr>
<tr><td>Isolate burst</td><td>Press, drag a loop around the burst, and release.</td></tr>
<tr><td>Ruler</td><td>Click two points to measure duration, frequency change, and slope.</td></tr>
<tr><td>Light-curve click</td><td>With click mode on, click a frequency to plot its light curve.</td></tr>
<tr><td>Annotations</td><td>Line: click start then end. Text: click the placement point. Polygon: click points, right-click to close.</td></tr>
</table>

<a name="solar-image-analysis"></a>
<h2>14. Solar Image Analysis</h2>
<p>Open from <code>Analysis &#8594; Solar Image Analysis</code>. A multi-mission imaging workspace for SDO/AIA,
STEREO/EUVI, GOES/SUVI, SOHO/LASCO, STEREO/COR and HI, and SDO/HMI data.</p>
<ul>
<li><b>Controls column</b> (left): numbered panels for <b>1 Data Source</b>, <b>2 Archive Results</b>, and
<b>3 Analysis</b>, plus Movie Export, Display and Crop, Coronagraph Tools, Heliospheric Imager (J-map),
Magnetic Vector Field (HMI), and Active Regions.</li>
<li><b>Canvas</b> (center): the image with a header readout (solar radius, position angle, pixel), a Measure
toolbar (Ruler, Profile, Region Stats, Track CME, Clear), and a playback bar below it.</li>
<li><b>Playback bar</b>: Rewind, Previous, Play, Pause, Next, a frame scrubber, a frame counter, and a speed (FPS) box.</li>
<li><b>Common actions</b>: fetch or find archive records, load local FITS, plot frames, running/base difference,
crop by ROI, detect bright active regions, fetch NOAA/HEK labels, build RGB composites, and export plots,
cropped FITS, CSV, GIF, or MP4.</li>
<li><b>Sessions</b>: save and reopen the workspace as an <code>.ecsolar</code> file
(<code>Ctrl+S</code> / <code>Ctrl+Shift+S</code>). Press {_kbd('Esc')} to cancel an in-progress measurement pick.</li>
</ul>
<p class="note">Cropping is done locally after files load; metadata overlays need network access, but region
detection works on local files.</p>

<a name="fits-downloader"></a>
<h2>15. FITS Downloader</h2>
<p>Open from <code>Download &#8594; Launch FITS Downloader</code> or
<code>Solar Events &#8594; Radio Bursts</code>. It has three tabs:</p>
<ul>
<li><b>Single Station</b>: pick station, date, and hour, show available files, then preview, download, compare,
or import them into the analyzer.</li>
<li><b>Multi-Station Event</b>: select stations and a UTC event window, search matching files, then download or
send them to the comparison workspace.</li>
<li><b>Spectral Overview</b>: generate a station's full UTC-day spectrum as six four-hour panels with a day-wide
median_dB baseline, with per-focus-code preview tabs, and export it.</li>
</ul>
<p>The separate <b>Learmonth</b> downloader loads or downloads the Learmonth daily archive, converts selected
chunks to FIT, and imports them for the same workflow used with e-CALLISTO data.</p>

<a name="context-viewers"></a>
<h2>16. Solar event context viewers</h2>
<ul>
<li><b>SOHO/LASCO CME Catalog</b>: daily CME lists, a parameter table, and associated LASCO movies.</li>
<li><b>GOES X-Ray Flux</b>: inspect X-ray time windows and flares, choose the spacecraft, and export the plot and
data. Legacy and modern GOES satellites are selected automatically for overlays.</li>
<li><b>GOES SEP Proton Flux</b>: plot proton flux near 10 MeV and 100 MeV across multi-day ranges, with a manual
spacecraft override and PNG/CSV export.</li>
<li><b>Kyoto Dst</b> and <b>GFZ Kp</b>: fetch geomagnetic indices over a UTC range with storm-level guides and
PNG/CSV export.</li>
</ul>

<a name="sunpy-explorer"></a>
<h2>17. SunPy Multi-Mission Explorer</h2>
<p>Open from <code>Solar Events &#8594; Archives</code>. Search external archives (SDO/AIA, SOHO/LASCO C2/C3,
STEREO-A/EUVI map products, and GOES/XRS time series), download into an app-managed cache, plot with frame
stepping and running difference, compute ROI statistics, and export plots and summaries. Archive search and
download need network access; cached files reopen offline.</p>

<a name="shortcuts"></a>
<h2>A. Keyboard shortcuts</h2>
<table>
<tr><th>Shortcut</th><th>Action</th></tr>
<tr><td>{_kbd('F1')}</td><td>Open this User Guide</td></tr>
<tr><td>{_kbd('Ctrl+O')}</td><td>Open a FITS file</td></tr>
<tr><td>{_kbd('Ctrl+E')}</td><td>Export figure</td></tr>
<tr><td>{_kbd('Ctrl+F')}</td><td>Export as FITS</td></tr>
<tr><td>{_kbd('Ctrl+S')}</td><td>Save Project (Save Session in the Solar window)</td></tr>
<tr><td>{_kbd('Ctrl+Shift+S')}</td><td>Save Project As / Save Session As</td></tr>
<tr><td>{_kbd('Ctrl+Shift+O')}</td><td>Open Project</td></tr>
<tr><td>{_kbd('Ctrl+Z')}</td><td>Undo</td></tr>
<tr><td>{_kbd('Ctrl+Shift+Z')}</td><td>Redo</td></tr>
<tr><td>{_kbd('Esc')}</td><td>Cancel a pick (Solar Image Analysis)</td></tr>
</table>

<a name="file-types"></a>
<h2>B. File types</h2>
<table>
<tr><th>Extension</th><th>Meaning</th></tr>
<tr><td><code>.fit .fits .fit.gz .fits.gz</code></td><td>Radio spectra loaded by the analyzer, and solar images in the imaging workspace.</td></tr>
<tr><td><code>.efaproj</code></td><td>Full analyzer project (view, processing, data, and analysis session).</td></tr>
<tr><td><code>.efaview.json</code></td><td>Portable view configuration (range, units, thresholds, colormap, styling).</td></tr>
<tr><td><code>.ecsolar</code></td><td>Solar Image Analysis session (embeds its FITS frames).</td></tr>
</table>

<a name="tips"></a>
<h2>C. Tips and troubleshooting</h2>
<ul>
<li><b>Greyed-out buttons?</b> Load a file first. Analysis controls and the Graph Properties panel activate once
data is present.</li>
<li><b>Rectangle zoom does nothing?</b> Click <b>Lock</b> on the toolbar first, then drag the rectangle.</li>
<li><b>Drift picking will not stop?</b> Right-click or double-click to finish the point series.</li>
<li><b>Restricted save folder on Windows?</b> If the default location (for example inside Program Files) is not
writable, the app prompts you to choose another folder.</li>
<li><b>Recovering after a crash?</b> Use <code>File &#8594; Recover Last Session</code> to restore the latest autosave.</li>
<li><b>Type II magnetic-field results</b> are experimental; confirm them against independently validated events
before drawing scientific conclusions.</li>
</ul>

<hr>
<p class="muted">{APP_NAME} version {APP_VERSION}. Developed by Sahan S Liyanage, Astronomical and Space
Science Unit, University of Colombo, Sri Lanka. Use <b>About &#8594; Cite this Software</b> for the recommended
citation.</p>
"""


USER_GUIDE_HTML = f"""
<a name="top"></a>
<h1>{APP_NAME}</h1>
<p class="lead">User Guide &#8226; version {APP_VERSION}</p>
<p>This guide explains how to load and view e-CALLISTO solar radio spectra, reduce noise, isolate and analyze
bursts, and use the built-in downloaders and solar-event tools. Start with the Quick Start, then use the
reference sections for details. Click any entry below to jump to it.</p>
{_TOC}
<hr>
{_BODY}
"""


def user_guide_html() -> str:
    """Return the full guide HTML (convenience accessor)."""

    return USER_GUIDE_HTML
