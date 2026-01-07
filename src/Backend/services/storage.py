from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Iterable, List


BASE_STORAGE = Path(os.environ.get("CALLISTO_STORAGE", "data/sessions"))


def ensure_storage_dir() -> Path:
    BASE_STORAGE.mkdir(parents=True, exist_ok=True)
    return BASE_STORAGE


def create_session() -> str:
    ensure_storage_dir()
    session_id = uuid.uuid4().hex
    session_path = BASE_STORAGE / session_id
    session_path.mkdir(parents=True, exist_ok=True)
    return session_id


def session_path(session_id: str) -> Path:
    ensure_storage_dir()
    return BASE_STORAGE / session_id


def list_session_files(session_id: str) -> List[str]:
    folder = session_path(session_id)
    if not folder.exists():
        return []
    return sorted([item.name for item in folder.iterdir() if item.is_file()])


def save_upload(session_id: str, filename: str, content: bytes) -> Path:
    folder = session_path(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / filename
    destination.write_bytes(content)
    return destination


def resolve_files(session_id: str, file_names: Iterable[str]) -> List[Path]:
    folder = session_path(session_id)
    return [folder / name for name in file_names]
