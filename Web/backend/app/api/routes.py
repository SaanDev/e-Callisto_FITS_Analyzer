from __future__ import annotations

from io import BytesIO
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.rate_limit import RateLimitExceeded
from app.core.sessions import DatasetNotLoadedError, SessionExpiredError, SessionNotFoundError
from app.domain.analyzer import fit_analyzer
from app.domain.exports import (
    export_analysis_figure,
    export_analyzer_xlsx,
    export_fits_file,
    export_maxima_csv,
    export_maxima_figure,
    export_spectrum_figure,
)
from app.domain.fits import header_summary
from app.domain.processing import background_reduce, build_spectrum_payload, extract_maxima_points
from app.schemas import (
    AnalyzerXlsxExportRequest,
    BackgroundRequest,
    FigureExportRequest,
    FitsExportRequest,
    FitRequest,
    MaximaCsvExportRequest,
    MaximaRequest,
    SessionResponse,
)


router = APIRouter(prefix="/api/v1")


def _settings(request: Request):
    return request.app.state.settings


def _sessions(request: Request):
    return request.app.state.sessions


def _limiter(request: Request):
    return request.app.state.rate_limiter


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _apply_rate_limit(
    request: Request,
    *,
    bucket: str,
    limit: int,
    window_seconds: int,
) -> None:
    try:
        _limiter(request).hit(
            f"{bucket}:{_client_key(request)}",
            limit=limit,
            window_seconds=window_seconds,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc


def _handle_session_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SessionExpiredError):
        return HTTPException(status_code=410, detail="Session expired.")
    if isinstance(exc, SessionNotFoundError):
        return HTTPException(status_code=404, detail="Session not found.")
    if isinstance(exc, DatasetNotLoadedError):
        return HTTPException(status_code=404, detail="No dataset is loaded for this session.")
    return HTTPException(status_code=500, detail="Unexpected session error.")


def _analysis_points(points: list[Any] | None) -> list[dict[str, float]] | None:
    if points is None:
        return None
    out: list[dict[str, float]] = []
    for point in points:
        if hasattr(point, "model_dump"):
            point = point.model_dump()
        out.append(
            {
                "timeChannel": float(point["timeChannel"]),
                "timeSeconds": float(point.get("timeSeconds", float(point["timeChannel"]) * 0.25)),
                "freqMHz": float(point["freqMHz"]),
            }
        )
    return out


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/sessions", response_model=SessionResponse)
async def create_session(request: Request) -> SessionResponse:
    settings = _settings(request)
    _apply_rate_limit(
        request,
        bucket="session-create",
        limit=settings.session_creations_per_minute,
        window_seconds=60,
    )
    session = _sessions(request).create_session()
    return SessionResponse(
        sessionId=session.session_id,
        expiresAt=session.expires_at(_sessions(request).ttl_seconds).isoformat(),
    )


@router.post("/sessions/{session_id}/dataset")
async def upload_dataset(
    session_id: str,
    request: Request,
    dataset: UploadFile = File(...),
) -> dict[str, Any]:
    settings = _settings(request)
    _apply_rate_limit(request, bucket="default", limit=settings.requests_per_minute, window_seconds=60)
    _apply_rate_limit(
        request,
        bucket="dataset-upload",
        limit=settings.dataset_uploads_per_ten_minutes,
        window_seconds=600,
    )

    total = 0
    chunks: list[bytes] = []
    while True:
        chunk = await dataset.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Uploaded FITS file exceeds the 64 MB limit.")
        chunks.append(chunk)

    try:
        session = _sessions(request).replace_dataset(
            session_id,
            filename=str(dataset.filename or "upload.fits"),
            content=b"".join(chunks),
        )
    except Exception as exc:
        handled = _handle_session_error(exc)
        if handled.status_code != 500:
            raise handled from exc
        raise HTTPException(status_code=400, detail=f"Invalid FITS file: {exc}") from exc

    data = session.dataset
    assert data is not None
    return {
        "filename": data.filename,
        "shape": [int(data.raw_data.shape[0]), int(data.raw_data.shape[1])],
        "freqRangeMHz": [float(data.freqs.min()), float(data.freqs.max())],
        "timeRangeSeconds": [float(data.time.min()), float(data.time.max())],
        "utStartSeconds": data.ut_start_seconds,
        "headerSummary": header_summary(data.header0),
        "rawSpectrum": build_spectrum_payload(
            label="Raw Spectrum",
            data=data.raw_data,
            freqs=data.freqs,
            time_axis=data.time,
        ),
    }


@router.post("/sessions/{session_id}/processing/background")
async def run_background_reduction(
    session_id: str,
    payload: BackgroundRequest,
    request: Request,
) -> dict[str, Any]:
    settings = _settings(request)
    _apply_rate_limit(request, bucket="default", limit=settings.requests_per_minute, window_seconds=60)
    try:
        dataset = _sessions(request).get_dataset(session_id)
    except Exception as exc:
        raise _handle_session_error(exc) from exc
    processed = background_reduce(
        dataset.raw_data,
        clip_low=payload.clipLow,
        clip_high=payload.clipHigh,
    )
    _sessions(request).update_processed_data(session_id, processed)
    return build_spectrum_payload(
        label="Background Subtracted",
        data=processed,
        freqs=dataset.freqs,
        time_axis=dataset.time,
    )


@router.post("/sessions/{session_id}/analysis/maxima")
async def extract_maxima(
    session_id: str,
    payload: MaximaRequest,
    request: Request,
) -> dict[str, Any]:
    settings = _settings(request)
    _apply_rate_limit(request, bucket="default", limit=settings.requests_per_minute, window_seconds=60)
    try:
        dataset = _sessions(request).get_dataset(session_id)
    except Exception as exc:
        raise _handle_session_error(exc) from exc

    source_name = str(payload.source).strip().lower()
    data = dataset.raw_data if source_name == "raw" else dataset.processed_data
    if data is None:
        raise HTTPException(status_code=400, detail="Processed data are not available yet.")

    points = extract_maxima_points(data, dataset.freqs)
    _sessions(request).remember_maxima(session_id, points)
    return {"source": source_name, "points": points}


@router.post("/sessions/{session_id}/analysis/fit")
async def run_fit(
    session_id: str,
    payload: FitRequest,
    request: Request,
) -> dict[str, Any]:
    settings = _settings(request)
    _apply_rate_limit(request, bucket="default", limit=settings.requests_per_minute, window_seconds=60)
    points = _analysis_points(payload.points)
    if not points:
        raise HTTPException(status_code=400, detail="At least two points are required for fitting.")
    try:
        _sessions(request).get_dataset(session_id)
    except Exception as exc:
        raise _handle_session_error(exc) from exc
    try:
        result = fit_analyzer(points, mode=payload.mode, fold=payload.fold)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Analyzer fit failed: {exc}") from exc
    _sessions(request).remember_maxima(session_id, points)
    _sessions(request).remember_analysis(session_id, result)
    return result


@router.post("/sessions/{session_id}/exports/figure")
async def export_figure(
    session_id: str,
    payload: FigureExportRequest,
    request: Request,
):
    settings = _settings(request)
    _apply_rate_limit(request, bucket="default", limit=settings.requests_per_minute, window_seconds=60)
    try:
        dataset = _sessions(request).get_dataset(session_id)
    except Exception as exc:
        raise _handle_session_error(exc) from exc

    try:
        if payload.plotKind == "spectrum":
            data, filename, media_type = export_spectrum_figure(
                dataset,
                source=payload.source,
                title=payload.title,
                fmt=payload.format,
            )
        elif payload.plotKind == "maxima":
            points = _analysis_points(payload.points) or dataset.last_maxima
            if not points:
                raise ValueError("No maxima points are available for export.")
            data, filename, media_type = export_maxima_figure(points, title=payload.title, fmt=payload.format)
        else:
            analysis = payload.analysisResult or dataset.last_analysis
            if not analysis:
                raise ValueError("No analyzer result is available for export.")
            data, filename, media_type = export_analysis_figure(
                analysis,
                plot_kind=payload.plotKind,
                title=payload.title,
                fmt=payload.format,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return StreamingResponse(
        BytesIO(data),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/sessions/{session_id}/exports/fits")
async def export_fits(
    session_id: str,
    payload: FitsExportRequest,
    request: Request,
):
    settings = _settings(request)
    _apply_rate_limit(request, bucket="default", limit=settings.requests_per_minute, window_seconds=60)
    try:
        dataset = _sessions(request).get_dataset(session_id)
        data, filename, media_type = export_fits_file(dataset, source=payload.source, bitpix=payload.bitpix)
    except Exception as exc:
        handled = _handle_session_error(exc)
        if handled.status_code != 500:
            raise handled from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StreamingResponse(
        BytesIO(data),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/sessions/{session_id}/exports/maxima-csv")
async def export_points_csv(
    session_id: str,
    payload: MaximaCsvExportRequest,
    request: Request,
):
    settings = _settings(request)
    _apply_rate_limit(request, bucket="default", limit=settings.requests_per_minute, window_seconds=60)
    try:
        dataset = _sessions(request).get_dataset(session_id)
    except Exception as exc:
        raise _handle_session_error(exc) from exc
    points = _analysis_points(payload.points) or dataset.last_maxima
    if not points:
        raise HTTPException(status_code=400, detail="No maxima points are available for CSV export.")
    data, filename, media_type = export_maxima_csv(points)
    return StreamingResponse(
        BytesIO(data),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/sessions/{session_id}/exports/analyzer-xlsx")
async def export_analysis_xlsx(
    session_id: str,
    payload: AnalyzerXlsxExportRequest,
    request: Request,
):
    settings = _settings(request)
    _apply_rate_limit(request, bucket="default", limit=settings.requests_per_minute, window_seconds=60)
    try:
        dataset = _sessions(request).get_dataset(session_id)
    except Exception as exc:
        raise _handle_session_error(exc) from exc
    analysis = payload.analysisResult or dataset.last_analysis
    if not analysis:
        raise HTTPException(status_code=400, detail="No analyzer result is available for XLSX export.")
    data, filename, media_type = export_analyzer_xlsx(
        analysis,
        source_filename=payload.sourceFilename,
    )
    return StreamingResponse(
        BytesIO(data),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

