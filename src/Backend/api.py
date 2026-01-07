from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, List, Optional

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.Backend.services import (
    combine_frequency_payload,
    combine_time_payload,
    create_job,
    create_session,
    fit_analysis,
    get_job,
    list_session_files,
    load_fits_payload,
    max_intensity_payload,
    reduce_noise_payload,
    save_upload,
    serialize_array,
    set_error,
    set_result,
    set_running,
)
from src.Backend.services.serialization import deserialize_array
from src.Backend.services.storage import resolve_files, session_path

matplotlib.use("Agg")

app = FastAPI(title="e-CALLISTO FITS Analyzer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = Path(__file__).resolve().parent.parent / "web_frontend"
if frontend_dir.exists():
    app.mount("/app", StaticFiles(directory=frontend_dir, html=True), name="frontend")


class NoiseReductionRequest(BaseModel):
    session_id: str
    file_name: str
    clip_low: float = -5
    clip_high: float = 20


class CombineRequest(BaseModel):
    session_id: str
    file_names: List[str]


class MaxIntensityRequest(BaseModel):
    session_id: str
    file_name: str
    data: Optional[Any] = None
    freqs: Optional[Any] = None


class FitRequest(BaseModel):
    time: Any
    freq: Any
    harmonic: bool = False


class ExportRequest(BaseModel):
    filename: str
    data: Any
    freqs: Optional[Any] = None
    time: Optional[Any] = None
    format: str = "csv"


@app.post("/sessions")
def create_session_endpoint():
    return {"session_id": create_session()}


@app.get("/sessions/{session_id}/files")
def list_files_endpoint(session_id: str):
    return {"files": list_session_files(session_id)}


@app.post("/sessions/{session_id}/upload")
async def upload_fits(session_id: str, file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file upload")
    saved_path = save_upload(session_id, file.filename, content)
    return {"file_name": saved_path.name}


@app.get("/sessions/{session_id}/download/{file_name}")
def download_file(session_id: str, file_name: str):
    path = session_path(session_id) / file_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


@app.post("/processing/noise-reduction")
def noise_reduction(request: NoiseReductionRequest):
    file_path = session_path(request.session_id) / request.file_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    payload = load_fits_payload(str(file_path))
    reduced = reduce_noise_payload(payload.data, request.clip_low, request.clip_high)
    return {
        "data": serialize_array(reduced),
        "freqs": serialize_array(payload.freqs),
        "time": serialize_array(payload.time),
        "filename": request.file_name,
    }


@app.post("/processing/combine-frequency")
def combine_frequency(request: CombineRequest):
    file_paths = resolve_files(request.session_id, request.file_names)
    if not all(path.exists() for path in file_paths):
        raise HTTPException(status_code=404, detail="One or more files not found")
    combined = combine_frequency_payload([str(path) for path in file_paths])
    return combined.to_serializable()


@app.post("/processing/combine-time")
def combine_time(request: CombineRequest):
    file_paths = resolve_files(request.session_id, request.file_names)
    if not all(path.exists() for path in file_paths):
        raise HTTPException(status_code=404, detail="One or more files not found")
    combined = combine_time_payload([str(path) for path in file_paths])
    return combined.to_serializable()


@app.post("/analysis/max-intensity")
def max_intensity(request: MaxIntensityRequest):
    if request.data and request.freqs:
        data = deserialize_array(request.data)
        freqs = deserialize_array(request.freqs)
    else:
        file_path = session_path(request.session_id) / request.file_name
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        payload = load_fits_payload(str(file_path))
        data = payload.data
        freqs = payload.freqs
    return max_intensity_payload(data, freqs)


@app.post("/analysis/fit")
def fit_endpoint(request: FitRequest):
    time = deserialize_array(request.time)
    freq = deserialize_array(request.freq)
    result = fit_analysis(time, freq, harmonic=request.harmonic)
    return result.to_serializable()


def _run_fit_job(job_id: str, time_payload: dict, freq_payload: dict, harmonic: bool) -> None:
    try:
        set_running(job_id)
        time = deserialize_array(time_payload)
        freq = deserialize_array(freq_payload)
        result = fit_analysis(time, freq, harmonic=harmonic)
        set_result(job_id, result.to_serializable())
    except Exception as exc:
        set_error(job_id, str(exc))


@app.post("/analysis/fit-job")
def fit_job_endpoint(request: FitRequest, background_tasks: BackgroundTasks):
    job_id = create_job()
    background_tasks.add_task(_run_fit_job, job_id, request.time, request.freq, request.harmonic)
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": job.status, "result": job.result, "error": job.error}


@app.post("/export/data")
def export_data(request: ExportRequest):
    data = deserialize_array(request.data)
    export_format = request.format.lower()
    if export_format not in {"csv", "xlsx"}:
        raise HTTPException(status_code=400, detail="Unsupported export format")

    frame = pd.DataFrame(data)
    buffer = BytesIO()
    if export_format == "csv":
        frame.to_csv(buffer, index=False)
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={request.filename}.csv"},
        )

    frame.to_excel(buffer, index=False)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={request.filename}.xlsx"},
    )


@app.post("/export/plot")
def export_plot(request: ExportRequest):
    data = deserialize_array(request.data)
    freqs = deserialize_array(request.freqs) if request.freqs else None
    time = deserialize_array(request.time) if request.time else None

    fig, ax = plt.subplots(figsize=(10, 6))
    if freqs is not None and time is not None:
        extent = [0, time[-1], freqs[-1], freqs[0]]
        ax.imshow(data, aspect="auto", extent=extent, cmap="viridis")
        ax.set_xlabel("Time [s]")
        ax.set_ylabel("Frequency [MHz]")
    else:
        ax.plot(np.arange(len(data)), data)
        ax.set_xlabel("Index")
        ax.set_ylabel("Value")

    ax.set_title(request.filename)

    buffer = BytesIO()
    export_format = request.format.lower()
    if export_format not in {"png", "pdf"}:
        raise HTTPException(status_code=400, detail="Unsupported export format")
    fig.savefig(buffer, format=export_format, dpi=300, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)

    media_type = "image/png" if export_format == "png" else "application/pdf"
    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={request.filename}.{export_format}"},
    )
