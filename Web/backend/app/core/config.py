from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "e-CALLISTO FITS Analyzer Web"
    runtime_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "runtime"
    )
    max_upload_bytes: int = 64 * 1024 * 1024
    session_ttl_seconds: int = 60 * 60
    requests_per_minute: int = 120
    session_creations_per_minute: int = 20
    dataset_uploads_per_ten_minutes: int = 10
    frontend_origin: str = "http://localhost:5173"

    @classmethod
    def from_env(cls) -> "Settings":
        runtime_default = Path(__file__).resolve().parents[3] / "runtime"
        runtime_raw = os.environ.get("ECALLISTO_WEB_RUNTIME_DIR", str(runtime_default))
        return cls(
            runtime_dir=Path(runtime_raw).expanduser().resolve(),
            max_upload_bytes=_env_int("ECALLISTO_WEB_MAX_UPLOAD_BYTES", 64 * 1024 * 1024),
            session_ttl_seconds=_env_int("ECALLISTO_WEB_SESSION_TTL_SECONDS", 60 * 60),
            requests_per_minute=_env_int("ECALLISTO_WEB_REQUESTS_PER_MINUTE", 120),
            session_creations_per_minute=_env_int(
                "ECALLISTO_WEB_SESSION_CREATIONS_PER_MINUTE", 20
            ),
            dataset_uploads_per_ten_minutes=_env_int(
                "ECALLISTO_WEB_DATASET_UPLOADS_PER_TEN_MINUTES", 10
            ),
            frontend_origin=os.environ.get(
                "ECALLISTO_WEB_FRONTEND_ORIGIN", "http://localhost:5173"
            ),
        )

