"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

"""
Compatibility facade for legacy imports from src.ui.gui_main.
"""

from src.ui.radio.dialogs.analyze_dialog import AnalyzeDialog
from src.ui.radio.dialogs.batch_processing_dialog import BatchProcessingDialog
from src.ui.help.bug_report_dialog import BugReportDialog
from src.ui.radio.dialogs.combine_dialogs import CombineFitsDialog, CombineFrequencyDialog, CombineTimeDialog
from src.ui.radio.dialogs.max_intensity_dialog import MaxIntensityPlotDialog
from src.ui.radio.dialogs.rfi_control_dialog import RFIControlDialog
from src.ui.radio.dialogs.type_ii_band_splitting_dialog import TypeIIBandSplittingDialog
from src.ui.common.gui_shared import (
    IS_LINUX,
    MplCanvas,
    _ext_from_filter,
    _install_linux_msgbox_fixer,
    pick_export_path,
    resource_path,
    start_combine,
)
from src.ui.app.gui_workers import DownloaderImportWorker, UpdateCheckWorker, UpdateDownloadWorker
from src.ui.app.main_window import MainWindow

__all__ = [
    "AnalyzeDialog",
    "BatchProcessingDialog",
    "BugReportDialog",
    "CombineFitsDialog",
    "CombineFrequencyDialog",
    "CombineTimeDialog",
    "DownloaderImportWorker",
    "IS_LINUX",
    "MainWindow",
    "MaxIntensityPlotDialog",
    "MplCanvas",
    "RFIControlDialog",
    "TypeIIBandSplittingDialog",
    "UpdateCheckWorker",
    "UpdateDownloadWorker",
    "_ext_from_filter",
    "_install_linux_msgbox_fixer",
    "pick_export_path",
    "resource_path",
    "start_combine",
]
