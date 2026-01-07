"""
Simple web application wrapper for the e-CALLISTO FITS Analyzer.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.Backend import burst_processor

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="e-CALLISTO FITS Analyzer (Web)")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _downsample_2d(data: np.ndarray, max_points: int) -> np.ndarray:
    if data.ndim != 2:
        raise ValueError("Expected 2D array for downsampling.")
    rows, cols = data.shape
    if rows * cols <= max_points:
        return data
    step = int(np.ceil(np.sqrt((rows * cols) / max_points)))
    return data[::step, ::step]


def _serialize_array(array: np.ndarray) -> list[list[float]]:
    return array.astype(float).tolist()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post("/api/load-fits")
async def load_fits(file: UploadFile = File(...), max_points: int = 250_000) -> JSONResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")
    if not file.filename.lower().endswith((".fit", ".fits", ".fit.gz")):
        raise HTTPException(status_code=400, detail="Unsupported FITS file type.")

    contents = await file.read()
    try:
        with io.BytesIO(contents) as buffer:
            data, freqs, time = burst_processor.load_fits(buffer)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read FITS: {exc}") from exc

    data_preview = _downsample_2d(data, max_points=max_points)

    payload: dict[str, Any] = {
        "shape": {"rows": int(data.shape[0]), "cols": int(data.shape[1])},
        "freqs": freqs.astype(float).tolist(),
        "time": time.astype(float).tolist(),
        "data": _serialize_array(data_preview),
        "preview": {
            "rows": int(data_preview.shape[0]),
            "cols": int(data_preview.shape[1]),
        },
    }
    return JSONResponse(content=payload)


@app.post("/api/reduce-noise")
async def reduce_noise(
    file: UploadFile = File(...),
    clip_low: float = -5.0,
    clip_high: float = 20.0,
    max_points: int = 250_000,
) -> JSONResponse:
    if clip_low >= clip_high:
        raise HTTPException(status_code=400, detail="clip_low must be less than clip_high.")

    contents = await file.read()
    try:
        with io.BytesIO(contents) as buffer:
            data, freqs, time = burst_processor.load_fits(buffer)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read FITS: {exc}") from exc

    processed = burst_processor.reduce_noise(data, clip_low=clip_low, clip_high=clip_high)
    processed_preview = _downsample_2d(processed, max_points=max_points)

    payload: dict[str, Any] = {
        "freqs": freqs.astype(float).tolist(),
        "time": time.astype(float).tolist(),
        "data": _serialize_array(processed_preview),
        "preview": {
            "rows": int(processed_preview.shape[0]),
            "cols": int(processed_preview.shape[1]),
        },
    }
    return JSONResponse(content=payload)
