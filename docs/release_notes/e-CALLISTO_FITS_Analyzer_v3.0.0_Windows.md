# e-CALLISTO FITS Analyzer v3.0.0 for Windows

## What's New
- Added a **Timeline** panel to the sidebar. A loaded dataset now knows the station, focus codes and observation times it came from, and **◀ Previous** / **Next ▶** fetch the adjacent 15-minute observation and time-combine it into the spectrum in place, one to eight steps per click. Following a burst across the archive no longer means returning to the downloader and re-importing every file.
- Extending preserves the work already done: annotations, the ruler measurement and drift picks survive, and their time coordinates shift with the axis when an earlier observation is prepended so they stay on their feature. The background subtraction, noise clip and RFI cleaning that were active are re-derived over the longer array instead of dropping the view back to Raw.
- Added **Trim Start** / **Trim End** to walk a dataset back, falling through to a plain single-file load at one segment. Extending and trimming are both a single **Ctrl+Z** away, restoring arrays, source list and annotations together.
- Adjacent observations are looked for among the loaded file's siblings on disk before any network request, so an already-downloaded set extends with no connection at all. Archive day listings are cached per session, and adjacent files can be prefetched in the background.
- Added an **Axis** section to the sidebar for switching the dynamic spectrum between **Linear** and **Log** frequency scales. A logarithmic axis spreads the decametric end of the band, where type II and type III bursts spend most of their drift, and places a harmonic pair at a constant separation.
- Log-axis ticks are anchored to the decades and labelled in MHz — 20, 30, 40, 50, 60, 70, 80 across a 20–80 MHz band, 50, 70, 100, 200, 300, 500, 700 across 45–870 — thinning as the span widens and falling back to round numbers over a band too narrow to hold one decade subdivision. The STEREO/SWAVES panel keeps its own native logarithmic axis in both modes.
- The single-station downloader now lists a day's files **grouped by focus code** rather than as one flat list, so the receivers that covered an event are visible at a glance.
- Previewing several selected files now previews them the way they would import: greedily combined where the selection is combinable in time, in frequency, or both, and as separate panels where it is not.
- Previewed and downloaded files are kept in a persistent on-disk cache, so re-previewing, importing or extending never fetches the same file twice.
- Added single-step **time × frequency merging**: a complete grid of timestamps and focus codes is frequency-combined per timestamp and then stitched in time in one operation, carrying the gap-fill and overlap policy through.
- A compatible selection from the multi-station event search can now be imported straight into the main window with automatic time-only, frequency-only or time + frequency combination. The comparison workspace opens only through the explicit **Compare** action.
- **Solar Image Analysis** gained calibration level selection — SDO/AIA at level 1 or 1.5, GOES/SUVI at level 1b or level 2 — with locally-prepared levels distinguished from archive-served ones.
- **Solar Image Analysis** gained differential-rotation compensation, pinning a selected region to the rotating solar surface across a multi-hour sequence: *track* moves the cut-out window and leaves pixel values untouched for photometry and light curves, *reproject* resamples each frame onto the reference-time grid so foreshortening is corrected for differencing.

## Bug Fixes and Improvements
- Sidebar sections in the main window and Solar Image Analysis are now collapsible cards with clickable headers. Expanded and collapsed state is remembered between sessions, and section contents were pared back so controls are readable rather than stacked.
- Fixed the logarithmic frequency axis drawing the spectrum upside down on the hardware-accelerated canvas: the image rectangle discarded the descending direction of a CALLISTO frequency axis and placed the highest frequency at the bottom.
- Fixed logarithmic tick placement on both renderers. Ticks were previously chosen by halving decades — labelling a 20–80 MHz band at 32 and 64 MHz — while the matplotlib canvas could leave a band containing no decade almost unlabelled. Both renderers now share one tick generator and frame an identical band, so **Reset Selection** settles correctly in either scale.
- Fixed frequency gap masks being applied to the wrong rows on a logarithmic axis, which placed the gap-fill band at incorrect frequencies on a frequency-combined dataset.
- Adding or trimming an observation now rescales the plot to the full dataset. The previous view was held steady, which left the newly fetched observation off the edge of the plot until the view was reset.
- The **Solar Image Analysis** menu entry no longer carries its instrument list, and the **Type II Band-splitting** window is no longer marked experimental.
- Reworked the compute backend integration and removed the deprecated compute kernels. Accelerated paths stay opt-in where they were measured to help.
- Expanded regression coverage for the timeline service, the archive cache, the linear/log frequency axis on both renderers, downloader focus grouping and combined previews, and the collapsible sidebar.

## Highlights
- Timeline extension, trimming and undo for following a burst across consecutive observations.
- Linear and logarithmic frequency axes on the dynamic spectrum, in both the software and hardware-accelerated renderers.
- STEREO/SWAVES radio spectrograph archive with a split CALLISTO + SWAVES view on a shared time axis.
- Multi-instrument coronagraph composites stacking SDO, STEREO and GOES/SUVI over SOHO/LASCO.
- Multi-station comparison workspace with synchronized panels and automatic combination.
- Type II burst band-splitting analysis for magnetic-field estimates. Confirm results against independently validated events before drawing scientific conclusions.
- GOES X-ray overlay support with automatic archive fallback across GOES-16 through GOES-19.
- Hardware-accelerated plotting with polygon, line, and text annotations, and rich text annotation editing.
- Project/session save and recovery, presets, provenance export, and analysis log export.

## Included Desktop Tools
- Solar Image Analysis (v1.5 beta) for SDO, SOHO/LASCO, STEREO and GOES/SUVI imaging workflows.
- e-CALLISTO and Learmonth Station data downloaders, and the STEREO/SWAVES archive.
- GOES X-ray, GOES SGPS proton flux, Kyoto Dst, and GFZ Kp viewers.
- Batch FITS processing for folder-based exports.
- In-app diagnostics bundle generation with **Report a Bug...**
- Project/session save and recovery workflows.

## Windows Notes
- Distributed as a native Windows installer: `e-CALLISTO_FITS_Analyzer_v3.0.0_Setup.exe`.
- In-app update checks support Windows release detection and installer download flow.
- Export handling is more robust when default save locations are restricted.
- The installer uses the standard Windows installation flow and can optionally create a desktop shortcut.
