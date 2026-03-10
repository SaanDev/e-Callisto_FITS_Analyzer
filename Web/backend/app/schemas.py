from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionResponse(BaseModel):
    sessionId: str
    expiresAt: str


class AnalysisPointModel(BaseModel):
    timeChannel: float
    timeSeconds: float | None = None
    freqMHz: float


class BackgroundRequest(BaseModel):
    clipLow: float = 0.0
    clipHigh: float = 0.0


class MaximaRequest(BaseModel):
    source: Literal["raw", "processed"] = "raw"


class FitRequest(BaseModel):
    points: list[AnalysisPointModel]
    mode: Literal["fundamental", "harmonic"] = "fundamental"
    fold: int = Field(default=1, ge=1, le=4)


class FigureExportRequest(BaseModel):
    source: Literal["raw", "processed"] = "raw"
    plotKind: Literal[
        "spectrum",
        "maxima",
        "best_fit",
        "shock_speed_vs_height",
        "shock_speed_vs_frequency",
        "shock_height_vs_frequency",
    ]
    format: Literal["png", "pdf", "eps", "svg", "tiff"] = "png"
    title: str | None = None
    points: list[AnalysisPointModel] | None = None
    analysisResult: dict[str, Any] | None = None


class FitsExportRequest(BaseModel):
    source: Literal["raw", "processed"] = "raw"
    bitpix: str | int = "auto"


class MaximaCsvExportRequest(BaseModel):
    points: list[AnalysisPointModel] | None = None


class AnalyzerXlsxExportRequest(BaseModel):
    analysisResult: dict[str, Any] | None = None
    sourceFilename: str

