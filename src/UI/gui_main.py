"""
Compatibility facade for legacy imports from src.UI.gui_main.
"""

from src.UI.dialogs.analyze_dialog import AnalyzeDialog
from src.UI.dialogs.batch_processing_dialog import BatchProcessingDialog
from src.UI.dialogs.combine_dialogs import CombineFrequencyDialog, CombineTimeDialog
from src.UI.dialogs.max_intensity_dialog import MaxIntensityPlotDialog
from src.UI.dialogs.rfi_control_dialog import RFIControlDialog
from src.UI.gui_shared import (
    IS_LINUX,
    MplCanvas,
    _ext_from_filter,
    _install_linux_msgbox_fixer,
    pick_export_path,
    resource_path,
    start_combine,
)
from src.UI.gui_workers import DownloaderImportWorker, UpdateCheckWorker, UpdateDownloadWorker
from src.UI.main_window import MainWindow

__all__ = [
    "AnalyzeDialog",
    "BatchProcessingDialog",
    "CombineFrequencyDialog",
    "CombineTimeDialog",
    "DownloaderImportWorker",
    "IS_LINUX",
    "MainWindow",
    "MaxIntensityPlotDialog",
    "MplCanvas",
    "RFIControlDialog",
    "UpdateCheckWorker",
    "UpdateDownloadWorker",
    "_ext_from_filter",
    "_install_linux_msgbox_fixer",
    "pick_export_path",
    "resource_path",
    "start_combine",
]
