from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.core.sessions import SessionStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    sessions = SessionStore(settings.runtime_dir, ttl_seconds=settings.session_ttl_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sessions.start_cleanup_loop()
        try:
            yield
        finally:
            sessions.stop_cleanup_loop()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter()
    app.state.sessions = sessions

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


app = create_app()
