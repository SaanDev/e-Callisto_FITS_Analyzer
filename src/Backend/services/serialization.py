from __future__ import annotations

from typing import Any, Dict, List, Sequence, Union

import numpy as np


def serialize_array(array: np.ndarray) -> Dict[str, Any]:
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "data": array.tolist(),
    }


def deserialize_array(payload: Union[Dict[str, Any], Sequence[float]]) -> np.ndarray:
    if isinstance(payload, dict):
        data = np.array(payload["data"], dtype=payload.get("dtype"))
        shape = payload.get("shape")
        if shape is not None:
            data = data.reshape(shape)
        return data
    return np.array(payload, dtype=float)


def serialize_optional_array(array: np.ndarray | None) -> Dict[str, Any] | None:
    if array is None:
        return None
    return serialize_array(array)


def normalize_array_list(values: List[float] | np.ndarray) -> np.ndarray:
    if isinstance(values, np.ndarray):
        return values
    return np.array(values, dtype=float)
