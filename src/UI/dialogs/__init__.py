"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

"""Dialog components for e-CALLISTO FITS Analyzer."""

from src.UI.dialogs.analyze_dialog import AnalyzeDialog
from src.UI.dialogs.annotation_text_dialog import TextAnnotationDialog
from src.UI.dialogs.batch_processing_dialog import BatchProcessingDialog
from src.UI.dialogs.bug_report_dialog import BugReportDialog
from src.UI.dialogs.citation_dialog import CitationDialog
from src.UI.dialogs.combine_dialogs import CombineFrequencyDialog, CombineTimeDialog
from src.UI.dialogs.max_intensity_dialog import MaxIntensityPlotDialog
from src.UI.dialogs.rfi_control_dialog import RFIControlDialog
from src.UI.dialogs.type_ii_band_splitting_dialog import TypeIIBandSplittingDialog

__all__ = [
    "AnalyzeDialog",
    "TextAnnotationDialog",
    "BatchProcessingDialog",
    "BugReportDialog",
    "CitationDialog",
    "CombineFrequencyDialog",
    "CombineTimeDialog",
    "MaxIntensityPlotDialog",
    "RFIControlDialog",
    "TypeIIBandSplittingDialog",
]
