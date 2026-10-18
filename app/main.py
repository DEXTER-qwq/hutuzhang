"""FastAPI application for 糊涂账."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import api, db

if getattr(sys, "frozen", False):
    STATIC = Path(sys._MEIPASS) / "app" / "static"  # type: ignore[attr-defined]
else:
    STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db()
        yield

    app = FastAPI(title="糊涂账", version="1.0.0", lifespan=lifespan)
    app.include_router(api.router)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.middleware("http")
    async def no_cache_assets(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": "1.0.0"}

    return app


app = create_app()
