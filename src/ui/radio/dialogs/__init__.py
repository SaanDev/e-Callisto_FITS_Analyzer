"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

"""Dialog components for e-CALLISTO FITS Analyzer."""

from src.ui.radio.dialogs.analyze_dialog import AnalyzeDialog
from src.ui.radio.dialogs.annotation_text_dialog import TextAnnotationDialog
from src.ui.radio.dialogs.batch_processing_dialog import BatchProcessingDialog
from src.ui.help.bug_report_dialog import BugReportDialog
from src.ui.help.citation_dialog import CitationDialog
from src.ui.radio.dialogs.combine_dialogs import CombineFitsDialog, CombineFrequencyDialog, CombineTimeDialog
from src.ui.radio.dialogs.display_range_dialog import DisplayRangeDialog
from src.ui.radio.dialogs.max_intensity_dialog import MaxIntensityPlotDialog
from src.ui.radio.dialogs.multi_station_comparison_dialog import MultiStationComparisonDialog
from src.ui.radio.dialogs.rfi_control_dialog import RFIControlDialog
from src.ui.radio.dialogs.type_ii_band_splitting_dialog import TypeIIBandSplittingDialog
from src.ui.radio.dialogs.type_ii_graph_settings_dialog import TypeIIGraphSettingsDialog
from src.ui.help.user_guide_dialog import UserGuideDialog

__all__ = [
    "AnalyzeDialog",
    "TextAnnotationDialog",
    "BatchProcessingDialog",
    "BugReportDialog",
    "CitationDialog",
    "CombineFitsDialog",
    "CombineFrequencyDialog",
    "CombineTimeDialog",
    "DisplayRangeDialog",
    "MaxIntensityPlotDialog",
    "MultiStationComparisonDialog",
    "RFIControlDialog",
    "TypeIIBandSplittingDialog",
    "TypeIIGraphSettingsDialog",
    "UserGuideDialog",
]
