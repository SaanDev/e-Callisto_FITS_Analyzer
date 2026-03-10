from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DatasetBundle:
    filename: str
    source_path: Path
    raw_data: np.ndarray
    freqs: np.ndarray
    time: np.ndarray
    header0: fits.Header
    ut_start_seconds: float | None
    uploaded_at: datetime = field(default_factory=utc_now)
    processed_data: np.ndarray | None = None
    last_maxima: list[dict[str, float]] = field(default_factory=list)
    last_analysis: dict[str, Any] | None = None

