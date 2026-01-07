from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from src.Backend import burst_processor
from src.Backend.services.serialization import serialize_array


@dataclass(frozen=True)
class FitsPayload:
    data: np.ndarray
    freqs: np.ndarray
    time: np.ndarray
    filename: str
    ut_start_sec: Optional[float]

    def to_serializable(self) -> Dict[str, object]:
        return {
            "data": serialize_array(self.data),
            "freqs": serialize_array(self.freqs),
            "time": serialize_array(self.time),
            "filename": self.filename,
            "ut_start_sec": self.ut_start_sec,
        }


def load_fits_payload(filepath: str) -> FitsPayload:
    data, freqs, time = burst_processor.load_fits(filepath)
    return FitsPayload(data=data, freqs=freqs, time=time, filename=filepath, ut_start_sec=None)


def reduce_noise_payload(data: np.ndarray, clip_low: float, clip_high: float) -> np.ndarray:
    return burst_processor.reduce_noise(data, clip_low=clip_low, clip_high=clip_high)


def combine_frequency_payload(file_paths: List[str]) -> FitsPayload:
    combined = burst_processor.combine_frequency(file_paths)
    return FitsPayload(
        data=combined["data"],
        freqs=combined["freqs"],
        time=combined["time"],
        filename=combined["filename"],
        ut_start_sec=combined.get("ut_start_sec"),
    )


def combine_time_payload(file_paths: List[str]) -> FitsPayload:
    combined = burst_processor.combine_time(file_paths)
    return FitsPayload(
        data=combined["data"],
        freqs=combined["freqs"],
        time=combined["time"],
        filename=combined["filename"],
        ut_start_sec=combined.get("ut_start_sec"),
    )


def max_intensity_payload(data: np.ndarray, freqs: np.ndarray) -> Dict[str, object]:
    time_channels = np.arange(data.shape[1])
    max_freqs = freqs[np.argmax(data, axis=0)]
    return {
        "time_channels": time_channels.tolist(),
        "max_freqs": max_freqs.tolist(),
    }
