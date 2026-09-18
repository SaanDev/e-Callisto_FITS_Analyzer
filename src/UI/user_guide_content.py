"""
e-CALLISTO FITS Analyzer
Version 3.0.0
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
&#8226; <a href="#gcs-fitting">15. GCS CME Fitting</a><br>
&#8226; <a href="#fits-downloader">16. FITS Downloader</a><br>
&#8226; <a href="#context-viewers">17. Solar event context viewers</a><br>
&#8226; <a href="#sunpy-explorer">18. SunPy Multi-Mission Explorer</a></p>
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
<li><b>Remove the background.</b> In the left sidebar's <b>Background Subtraction</b> section, pick a
method (Mean, Median, or Median (dB)) and click <b>Subtract Background</b>. It is always computed from the raw
data, so clicking again with another method replaces the result rather than stacking on it.</li>
<li><b>Adjust the contrast.</b> Drag the <b>Lower Threshold</b> and <b>Upper Threshold</b> sliders. They only
set the color-scale limits and update live, so you can watch the burst emerge; the data are never modified.</li>
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
<li><b>Left sidebar</b>: grouped controls for the timeline, background subtraction, thresholds, units, graph appearance, the analysis summary,
and ruler readouts. A vertical arrow button on its edge hides or shows the sidebar to give the plot more room.</li>
<li><b>Viewer</b> (center): the dynamic spectrum. A status bar at the bottom shows messages on the left and,
on the right, the live cursor readout (time, frequency, intensity) and the update-check status.</li>
</ul>

<a name="menu-file"></a>
<h2>3. File menu</h2>
<table>
<tr><th>Item</th><th>What it does</th></tr>
<tr><td><b>Open</b> ({_kbd('Ctrl+O')})</td><td>Load one FITS file, or automatically combine a compatible multi-file time, frequency, or time + frequency selection.</td></tr>
<tr><td><b>Combine FITS Files...</b></td><td>Select, validate, preview, and import time-only, frequency-only, or complete timestamp × focus-code grid combinations.</td></tr>
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
<code>Solar Events &#8594; Radio Bursts</code>). See <a href="#fits-downloader">section 16</a>.</p>

<a name="menu-solar-events"></a>
<h2>6. Solar Events menu</h2>
<p>Opens external solar and geophysical data tools, grouped in submenus:</p>
<ul>
<li><b>CMEs &#8594; SOHO/LASCO CME Catalog</b>: daily CME lists, parameter table, and LASCO movies.</li>
<li><b>Flares &#8594; GOES X-Ray Flux</b>: the standalone X-ray viewer.</li>
<li><b>Energetic Particles &#8594; GOES SEP Proton Flux</b>: NOAA SGPS proton flux across a date range.</li>
<li><b>Geomagnetic</b>: Kyoto Dst Index and GFZ Kp Index viewers.</li>
<li><b>Archives &#8594; SunPy Multi-Mission Explorer</b>: search and plot external imagery and time series
(see <a href="#sunpy-explorer">section 18</a>).</li>
<li><b>Radio Bursts</b>: the e-CALLISTO, Learmonth, and STEREO/SWAVES loaders.</li>
<li><b>Sync Current Time Window</b>: push the analyzer time window to supported context viewers.</li>
<li><b>GOES Overlay</b>: draw the GOES long channel (XRS-B) and/or short channel (XRS-A) directly on the
spectrum, with a flare-class guide (A/B/C/M/X). The overlay does not alter the data.</li>
<li><b>SWAVES Panel</b>: show or hide the loaded STEREO/SWAVES spectrum without discarding it
(see <a href="#swaves">section 16a</a>).</li>
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
<li><b>GCS CME Fitting...</b>: reconstruct a CME in 3-D from three coronagraph viewpoints, starting from the
visible time range of the spectrum (see <a href="#gcs-fitting">section 15</a>).</li>
<li><b>Maximum Intensities &#8594; Open Maximum Intensities</b>: trace the peak frequency for each time channel
after noise reduction or burst isolation. Inside that window you can lasso-select outliers and remove them.</li>
<li><b>Type II Band-splitting &#8594; Open Type II Band-splitting</b>: pick points along the upper and lower
bands, fit both, and derive shock speed, height, bandwidth, compression ratio, Alfven Mach number, Alfven speed,
and magnetic field. Validate results against known events.</li>
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
<li><b>Background Subtraction</b>: choose <b>Mean</b> or <b>Median</b> to subtract each channel's mean or
median (the result stays in Digits), or <b>Median (dB)</b> to subtract each channel's median and convert to dB above
that background, as the Plotutil recipe and the batch processor do. Click <b>Subtract Background</b> to apply.
The subtraction always starts from the raw data, so re-applying replaces the previous result, and RFI cleaning or
an isolated burst made from the old result is cleared. While a Median (dB) result is shown, the Digits/dB switch is
locked to dB because the data are already in dB. <code>Edit &#8594; Reset to Raw</code> removes the subtraction.</li>
<li><b>Noise Clipping Thresholds</b>: the <b>Lower</b> and <b>Upper</b> sliders set the color-scale limits
(Vmin/Vmax) live, for contrast only &#8212; the data are never clipped or changed. With both at zero the color
scale fits the whole data range. Limits chosen on raw data reset when you subtract the background, since they
would saturate the new scale. The <b>Logarithmic Threshold Scale</b> checkbox gives finer control near zero.</li>
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
SOHO/EIT, STEREO/EUVI, GOES/SUVI, SOHO/LASCO, STEREO/COR and HI, and SDO/HMI data.</p>
<p><b>SOHO/EIT products.</b> The archive lists two files per EIT observation - the calibrated Level 1 FITS and
the raw level-zero file - and ignores a processing-level filter, so the window chooses one per observation:
Level 1 where it exists, the raw file otherwise. Level 1 processing runs about a year behind the raw archive,
so recent searches return raw frames and older ones calibrated frames; either way you get one frame per
observation. Because the two products differ in calibration and data type, a window that straddles the
boundary loads as two configuration groups. <b>Live Preview</b> opens Helioviewer quicklook imagery for both
SOHO instruments, which is the only way to see the most recent EIT frames.</p>
<ul>
<li><b>Controls column</b> (left): numbered panels for <b>1 Data Source</b>, <b>2 Archive Results</b>, and
<b>3 Analysis</b>, plus Movie Export, Display and Crop, Coronagraph Tools, Heliospheric Imager (J-map),
Magnetic Vector Field (HMI), and Active Regions.</li>
<li><b>Canvas</b> (center): the image with a header readout (solar radius, position angle, pixel), a Measure
toolbar (Ruler, Profile, Region Stats, Track CME, Circle Fit, Clear), and a playback bar below it.</li>
<li><b>CME tracking panel</b> (right of the canvas): the per-frame table, a live height–time graph and a
<b>Fit</b> dropdown for a linear, quadratic or cubic fit. Linear gives one speed (with the acceleration from a
companion quadratic fit); the curved fits report the speed at both ends of the track, and cubic adds the jerk.
Every value comes with a 1σ error from the fit covariance — a degree-n fit needs n+1 points, and n+2 before an
error bar can be estimated. <b>Export</b> saves the table as CSV, or the graph with its fit as PNG, PDF, EPS, SVG,
TIFF or JPG in light mode and the OriginPro style.</li>
<li><b>Playback bar</b>: Rewind, Previous, Play, Pause, Next, a frame scrubber, a frame counter, and a speed (FPS) box.</li>
<li><b>Common actions</b>: fetch or find archive records, load local FITS, plot frames, running/base difference,
crop by ROI, detect bright active regions, fetch NOAA/HEK labels, build RGB composites, and export plots,
cropped FITS, CSV, GIF, or MP4.</li>
<li><b>Sessions</b>: save and reopen the workspace as an <code>.ecsolar</code> file
(<code>Ctrl+S</code> / <code>Ctrl+Shift+S</code>). Press {_kbd('Esc')} to cancel an in-progress measurement pick.</li>
<li><b>GCS CME Fitting</b>: <code>Analysis &#8594; GCS CME Fitting...</code> opens the three-viewpoint fitting window
at the time of the displayed frame (see <a href="#gcs-fitting">section 15</a>).</li>
</ul>
<p class="note">Cropping is done locally after files load; metadata overlays need network access, but region
detection works on local files.</p>

<a name="gcs-fitting"></a>
<h2>15. GCS CME Fitting</h2>
<p class="lead">Reconstruct a CME in three dimensions by fitting one Graduated Cylindrical Shell (GCS) to three
coronagraph views at once, and optionally a spheroid or ellipsoid to the shock it drives.</p>
<p>Open from <code>Analysis &#8594; GCS CME Fitting...</code> in the main window or in Solar Image Analysis. The
window downloads its own images, so nothing needs to be loaded first. From the analyzer, the visible time range of
the spectrum becomes the event range; from Solar Image Analysis, the event is centered on the displayed frame, one
hour either side. Choosing the menu item again reuses the open window and passes it the new time.</p>
<p>A single view cannot separate a CME's direction from its size: a wide CME aimed at the observer and a narrow one
crossing the sky look alike. Two well-separated views break that ambiguity and a third over-constrains it, which is
why one set of model controls drives all three panels.</p>

<h3>Load the viewpoints</h3>
<ul>
<li><b>Channels</b>: panels A, B and C default to STEREO-B COR2, SOHO LASCO C2 and STEREO-A COR2, left to right.
Any panel can be switched to STEREO-A or STEREO-B COR1/COR2 or LASCO C2/C3; channels with no data for the event
date are greyed out. The STEREO-B archive ends on 2014-09-27, so for later events panel A reports that it has no
data. Only SOHO and STEREO-A remain as vantage points then: another channel in panel A (LASCO C3 or COR1-A) adds an
image but not a new viewing direction.</li>
<li><b>Event range</b>: set the UTC start and end in the <b>Event &amp; channels</b> card, up to three days.
Changing it sets every channel's range, and each channel's range can then be adjusted on its own. A 15-minute
e-CALLISTO file spans only one or two coronagraph images (about one every 15 minutes for COR2 and every 12 for
LASCO C2), so widen the range to follow the CME. The frame cap (60 per channel by default) thins a longer sequence
evenly across the event rather than cutting it short.</li>
<li><b>Load</b>: click <b>Load all</b> ({_kbd('Ctrl+L')}) or a channel's own <b>Load</b> button. The images are
Helioviewer JPEG2000 files, so this needs network access; downloaded frames are cached and reused.
<code>Event &#8594; Cancel downloads</code> stops a slow load. Changing a channel's source or range discards its
loaded images.</li>
</ul>

<h3>Read the images</h3>
<ul>
<li><b>Shared time</b>: the time slider runs over the image times of all channels, and each panel shows its own
image nearest the selected time. The banner across each image gives the instrument, the actual observation time,
its offset from the shared time and the difference reference. <b>Max time offset</b> (5 minutes by default)
decides which panels take part in the fit; the models are still drawn on the others for comparison. Use a smaller
offset for a fast-evolving CME.</li>
<li><b>View</b>: <b>Raw</b>, <b>Running diff</b> (selected whenever frames load) or <b>Base diff</b> on the
toolbar. The first image of each channel is differenced against the archive frame just before the range, and is
shown raw only when no earlier frame exists.</li>
<li><b>Layout</b>: <b>Equal</b> shows three equal images with the controls beneath; <b>A</b>, <b>B</b> or
<b>C</b> enlarges that panel, stacks the other two beside it and moves the controls to the right. The toolbar also
toggles the solar limb, arcsecond axes and the image banners.</li>
<li><b>Display</b> card: wireframe color, width, opacity and mesh density, and each image's colormap and contrast
(choose the image with A, B or C).</li>
</ul>

<h3>Fit the flux rope</h3>
<ol>
<li><b>Align the shell by eye.</b> With <b>Edit: GCS flux rope</b> selected, set the six sliders, which also accept
typed values: <b>Lon</b> and <b>Lat</b> (Stonyhurst direction of travel), <b>Tilt</b> (rotation of the shell about
that direction), <b>Height</b> (the leading edge's distance from Sun center in R&#9737;, not the altitude above the
surface), <b>&#945;</b> (half angle between the legs) and <b>&#954;</b> (aspect ratio). Set the direction, tilt,
&#945; and &#954; before fine-tuning the height. You can also drag the apex handle in any panel: moving it around
the Sun turns the direction, and moving it outward or inward changes the height. The readout under the sliders
gives the derived leg height, apex radius, center distance, and face-on and edge-on widths.</li>
<li><b>Click the front.</b> Left-click points along the same ejecta front in two or more views. Right-click removes
that panel's last point, <b>Undo point</b> ({_kbd('Ctrl+Z')}) removes the last point clicked in any panel, and
<b>Clear points</b> removes them all. Points belong to the image they were clicked on and return when it is shown
again. Untick <b>Pick front points</b> to stop adding points.</li>
<li><b>Refine.</b> <b>Refine fit</b> ({_kbd('Ctrl+R')}) makes a local least-squares adjustment through the
points, so start close to the CME. It is available as soon as images are loaded and fits as many parameters as the
points support: 2 points fit the height, 4 in two separated views add the direction, then the tilt, &#945; and
&#954; follow one point at a time (7 points for all six). The status bar names what was fitted and how many more
points would free the next parameter. Formal errors are withheld when the local solution is unreliable.</li>
<li><b>Commit.</b> <b>Commit GCS</b> ({_kbd('Ctrl+Return')}) records the fit, and the images it was made from, at
the shared time. Step to later times and repeat to build a height&#8211;time series.</li>
</ol>
<p class="note">The status bar reports the views taking part, their separations and the number of points. With a
single view, or no pair of views between 20&#176; and 160&#176; apart, the direction cannot be constrained and is
held fixed.</p>

<h3>Recorded fits and kinematics</h3>
<ul>
<li>The <b>Kinematics</b> card lists the recorded fits beside a height&#8211;time plot. Choose a Linear, Quadratic
or Cubic fit and click <b>Fit height&#8211;time</b>; a fit of degree n needs n+1 recorded times, and n+2 before
errors can be estimated. Heights are de-projected under the model, so the speeds are model-dependent radial speeds
rather than plane-of-sky ones.</li>
<li>Double-click a row to go back to its time and restore its model. The <b>Fit</b> menu also restores or deletes
the fit recorded at the current time and resets the model.</li>
<li>One combination of images cannot be recorded at two different times, so repeated images never count as
independent height measurements.</li>
<li>Stepping, dragging the time slider and playback draw the recorded shells: each fit on its own images,
interpolated between recorded times (a display, not a fit) and held before the first and after the last. A model
with nothing recorded keeps its slider values, so commit before stepping away.</li>
<li>Moving the event range to a different event sets its recorded fits aside. They return when you come back to
that event, and the JSON export includes them.</li>
</ul>

<h3>Fit the shock</h3>
<p>Choose <b>Edit: Shock</b> to fit the shock the CME drives, its faint outer envelope, with a <b>Spheroid</b> or
an <b>Ellipsoid</b>. It is drawn in every view alongside the GCS shell in its own color (sky blue by default; the
shell is orange). The parameters follow PyThea: <b>Height</b> is the apex distance from Sun center,
<b>&#954;</b> = b/(height &#8722; 1 R&#9737;) sets the lateral size, <b>&#949;</b> stretches the shock radially
when positive and flattens it when negative, and an ellipsoid adds <b>&#945; (b/c)</b> and a <b>Tilt</b> about the
radial axis. Click the shock front, then use <b>Refine fit</b> and <b>Commit Shock</b> as for the flux rope. The
shock keeps its own front points, recorded fits, kinematics and CSV. With fewer than three views, &#949; and the
tilt are weakly constrained and an ellipsoid's shape parameters trade off against each other, so prefer a
spheroid.</p>

<h3>Export</h3>
<p>Images and graphs are drawn again from the data rather than copied from the screen, always on a white page
(light mode) whatever the application theme. Graphs follow the OriginPro style. Figures can be saved as PNG, PDF,
EPS, SVG, TIFF or JPG.</p>
<ul>
<li><code>File &#8594; Export analysis JSON</code> ({_kbd('Ctrl+Shift+S')}): the current, recorded and set-aside
parameters of both models, with the clicked points, image times, offsets and observer geometry. It is an analyzer
export, not a PyThea session file.</li>
<li><code>File &#8594; Export recorded fits CSV</code>, or <b>Export &#8594; Table as CSV</b> in the Kinematics
card: the recorded series of the model being edited.</li>
<li><code>File &#8594; Export height&#8211;time graph</code>, or <b>Export &#8594; Graph</b> in the Kinematics card:
the recorded apex heights with their error bars and the fit chosen in the <b>Fit</b> dropdown, under a title naming
the model and the fit, with the speed and acceleration in the legend.</li>
<li><code>File &#8594; Save viewpoint snapshot</code>: the three views as shown, with arcsec axes, the wireframes,
the solar limb and the front points, and a note of where each drawn shell comes from. Hiding the image banners
leaves only each panel's name above it.</li>
<li><code>File &#8594; Export movie (GIF/MP4)</code>: every time step as playback shows it, recorded shells
included, at the playback speed set in the toolbar. MP4 needs the bundled FFmpeg; a GIF is offered otherwise.</li>
<li><code>File &#8594; Export fitting report (PDF)</code>: the viewpoints and their separations, the model at the
current time, every recorded fit drawn on the images it was made from with its parameters and observation details,
each model's linear, quadratic and cubic height&#8211;time fits (a titled graph and every parameter of each, then the
three compared), and the method, its limits and references. Every page carries the application's name in the header
and the author's in the footer.</li>
</ul>
<p class="note">Stepping through time for a movie or a report leaves the window as it was: the time shown, the
working models, their formal errors and the front points all come back.</p>

<h3>Keys and limits</h3>
<p>While an image has focus, {_kbd('Space')} plays or pauses, the arrow keys step, {_kbd('Home')} returns to the
first frame, {_kbd('B')} toggles the image banners, {_kbd('0')} restores the equal layout and {_kbd('1')} to
{_kbd('3')} enlarge panels A to C. {_kbd('Ctrl+G')} jumps to a typed UTC time.
<code>Help &#8594; Fitting workflow and scientific limits</code> ({_kbd('F1')} in this window) summarizes the
procedure.</p>
<p class="note">GCS models the flux rope, not the shock. Formal fit errors leave out the uncertainty from choosing
the front, non-simultaneous images, image preparation and the assumed geometry, and a small residual does not make a
fit unique or accurate. Helioviewer JPEG2000 images are display products: suitable for fitting shapes, not for
calibrated intensity measurements.</p>

<a name="fits-downloader"></a>
<h2>16. FITS Downloader</h2>
<p>Open from <code>Download &#8594; Launch FITS Downloader</code> or
<code>Solar Events &#8594; Radio Bursts</code>. It has three tabs:</p>
<ul>
<li><b>Single Station</b>: pick station, date, and hour, show available files, then preview, download, compare,
or import them into the analyzer.</li>
<li><b>Multi-Station Event</b>: select stations and a UTC event window, search matching files, then download,
import compatible selections with automatic time/frequency combination, or open the comparison workspace with
the explicit <b>Compare</b> button.</li>
<li><b>Spectral Overview</b>: generate a station's full UTC-day spectrum as six four-hour panels with a day-wide
median_dB baseline, with per-focus-code preview tabs, and export it.</li>
</ul>
<p>The separate <b>Learmonth</b> downloader loads or downloads the Learmonth daily archive, converts selected
chunks to FIT, and imports them for the same workflow used with e-CALLISTO data.</p>

<a name="swaves"></a>
<h2>16a. STEREO/SWAVES dynamic spectrum</h2>
<p>Open from <code>Solar Events &#8594; Radio Bursts &#8594; SWAVES</code>. SWAVES covers 2.6 kHz to 16 MHz from
space, directly below the CALLISTO band, so a burst that drifts out of the ground-based range can be followed
into the interplanetary medium on the same figure.</p>
<ul>
<li><b>Window</b>: choose a UTC start date and time plus a duration. Data are one-minute averages served as one
file per UTC day; a window that crosses midnight fetches and stitches both days automatically. The archive
starts on 2006-10-27.</li>
<li><b>Spacecraft</b>: STEREO-A (Ahead) or STEREO-B (Behind). Behind is disabled for dates after 2014-10-01,
when contact with that spacecraft was lost.</li>
<li><b>Use CALLISTO Window</b>: copy the loaded spectrum's time range, widened on both sides by the
<b>Sync padding</b> value (30 minutes by default). The padding matters because a type II or III burst takes
tens of minutes to hours to drift from the corona down into the SWAVES band, so an exact match would cut off
the part you want to see. <code>Solar Events &#8594; Sync Current Time Window</code> updates an open SWAVES
dialog the same way.</li>
</ul>
<p>Once loaded, the plotting area splits: CALLISTO above, SWAVES below, on a shared time axis, so panning or
zooming either panel moves both. The CALLISTO interval is outlined on the SWAVES panel. The SWAVES frequency
axis is logarithmic and its intensity is decibels above the instrument background, so it keeps its own colorbar
and color scaling while following the colormap chosen in the sidebar. Loading SWAVES with no CALLISTO file open
plots it across the full area instead; it folds into the split view as soon as a FITS file is loaded.</p>
<p>The split view is included in <b>Save Plot</b> exports, saved in and restored from project files without
re-downloading, and added to generated PDF reports. The drawing, lasso, drift, and measurement tools still act
on the CALLISTO panel only. Data-reduction controls do not yet apply to the SWAVES panel.</p>

<a name="context-viewers"></a>
<h2>17. Solar event context viewers</h2>
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
<h2>18. SunPy Multi-Mission Explorer</h2>
<p>Open from <code>Solar Events &#8594; Archives</code>. Search external archives (SDO/AIA, SOHO/EIT, SOHO/LASCO C2/C3,
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
<p class="note">The GCS CME Fitting window has its own keys; see <a href="#gcs-fitting">section 15</a>.</p>

<a name="file-types"></a>
<h2>B. File types</h2>
<table>
<tr><th>Extension</th><th>Meaning</th></tr>
<tr><td><code>.fit .fits .fit.gz .fits.gz</code></td><td>Radio spectra loaded by the analyzer, and solar images in the imaging workspace. Upper-case suffixes (<code>.FITS</code>) open the same way.</td></tr>
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
<li><b>Type II magnetic-field results</b> should be confirmed against independently validated events
before drawing scientific conclusions.</li>
<li><b>GCS panel says it has no data?</b> The STEREO-B archive ends in September 2014; switch that panel to
another channel (see <a href="#gcs-fitting">section 15</a>).</li>
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
