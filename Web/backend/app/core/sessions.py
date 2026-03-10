from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import threading
import uuid
from typing import Any

import numpy as np

from app.domain.fits import load_callisto_fits
from app.domain.types import DatasetBundle


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionNotFoundError(KeyError):
    pass


class SessionExpiredError(RuntimeError):
    pass


class DatasetNotLoadedError(RuntimeError):
    pass


@dataclass
class SessionState:
    session_id: str
    root_dir: Path
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    dataset: DatasetBundle | None = None

    def touch(self) -> None:
        self.updated_at = utc_now()

    def expires_at(self, ttl_seconds: int) -> datetime:
        return self.updated_at + timedelta(seconds=int(ttl_seconds))


class SessionStore:
    def __init__(self, runtime_dir: Path, ttl_seconds: int) -> None:
        self._runtime_dir = Path(runtime_dir)
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        self._ttl_seconds = int(ttl_seconds)
        self._cleanup_interval_seconds = max(30, min(self._ttl_seconds, 300))
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.RLock()
        self._cleanup_stop = threading.Event()
        self._cleanup_thread: threading.Thread | None = None

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def start_cleanup_loop(self) -> None:
        with self._lock:
            if self._cleanup_thread and self._cleanup_thread.is_alive():
                return
            self._cleanup_stop.clear()
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_worker,
                name="ecallisto-web-session-cleanup",
                daemon=True,
            )
            self._cleanup_thread.start()

    def stop_cleanup_loop(self, timeout: float = 2.0) -> None:
        self._cleanup_stop.set()
        thread = self._cleanup_thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._cleanup_thread = None

    def create_session(self) -> SessionState:
        with self._lock:
            self.cleanup_expired()
            session_id = uuid.uuid4().hex
            root_dir = self._runtime_dir / session_id
            root_dir.mkdir(parents=True, exist_ok=True)
            session = SessionState(session_id=session_id, root_dir=root_dir)
            self._sessions[session_id] = session
            return session

    def get_session(self, session_id: str, *, touch: bool = True) -> SessionState:
        with self._lock:
            session = self._sessions.get(str(session_id))
            if session is None:
                raise SessionNotFoundError(session_id)
            if session.expires_at(self._ttl_seconds) <= utc_now():
                self._remove_session(session)
                raise SessionExpiredError(session_id)
            if touch:
                session.touch()
            return session

    def replace_dataset(
        self,
        session_id: str,
        *,
        filename: str,
        content: bytes,
    ) -> SessionState:
        with self._lock:
            session = self.get_session(session_id)
            self._clear_session_dir(session.root_dir)

            safe_name = Path(str(filename or "upload.fits")).name or "upload.fits"
            upload_path = session.root_dir / safe_name
            upload_path.write_bytes(content)

            loaded = load_callisto_fits(str(upload_path), memmap=False)
            session.dataset = DatasetBundle(
                filename=safe_name,
                source_path=upload_path,
                raw_data=np.asarray(loaded.data, dtype=np.float32),
                freqs=np.asarray(loaded.freqs, dtype=np.float32),
                time=np.asarray(loaded.time, dtype=np.float32),
                header0=loaded.header0,
                ut_start_seconds=loaded.ut_start_seconds,
            )
            session.touch()
            return session

    def get_dataset(self, session_id: str) -> DatasetBundle:
        session = self.get_session(session_id)
        if session.dataset is None:
            raise DatasetNotLoadedError("No dataset loaded for this session.")
        return session.dataset

    def update_processed_data(self, session_id: str, data: np.ndarray) -> DatasetBundle:
        with self._lock:
            dataset = self.get_dataset(session_id)
            dataset.processed_data = np.asarray(data, dtype=np.float32)
            session = self._sessions[session_id]
            session.touch()
            return dataset

    def remember_maxima(self, session_id: str, points: list[dict[str, float]]) -> DatasetBundle:
        with self._lock:
            dataset = self.get_dataset(session_id)
            dataset.last_maxima = list(points)
            self._sessions[session_id].touch()
            return dataset

    def remember_analysis(self, session_id: str, result: dict[str, Any]) -> DatasetBundle:
        with self._lock:
            dataset = self.get_dataset(session_id)
            dataset.last_analysis = dict(result)
            self._sessions[session_id].touch()
            return dataset

    def cleanup_expired(self) -> int:
        with self._lock:
            removed = 0
            deadline = utc_now()
            expired_ids: list[str] = []
            for session_id, session in list(self._sessions.items()):
                if session.expires_at(self._ttl_seconds) <= deadline:
                    expired_ids.append(session_id)
            for session_id in expired_ids:
                session = self._sessions.get(session_id)
                if session is None:
                    continue
                self._remove_session(session)
                removed += 1
            return removed

    def _remove_session(self, session: SessionState) -> None:
        self._sessions.pop(session.session_id, None)
        shutil.rmtree(session.root_dir, ignore_errors=True)

    def _clear_session_dir(self, root_dir: Path) -> None:
        for child in root_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    def _cleanup_worker(self) -> None:
        while not self._cleanup_stop.wait(self._cleanup_interval_seconds):
            self.cleanup_expired()
