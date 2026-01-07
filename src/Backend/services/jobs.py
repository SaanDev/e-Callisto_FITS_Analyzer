from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class JobStatus:
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


_jobs: Dict[str, JobStatus] = {}
_lock = threading.Lock()


def create_job() -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = JobStatus(status="queued")
    return job_id


def set_running(job_id: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].status = "running"


def set_result(job_id: str, result: Dict[str, Any]) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].status = "completed"
            _jobs[job_id].result = result


def set_error(job_id: str, error: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].status = "failed"
            _jobs[job_id].error = error


def get_job(job_id: str) -> Optional[JobStatus]:
    with _lock:
        return _jobs.get(job_id)
