"""Application factory: run migrations, mount every discovered module.

Run with:  uvicorn --factory mealplanner.main:create_app
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import mealplanner.modules
from mealplanner.core.db import apply_migrations, connect
from mealplanner.core.errors import Conflict, DomainError, Invalid, NotFound
from mealplanner.core.module import Module, discover_modules
from mealplanner.core.schema import CORE_MIGRATIONS, CORE_OWNER

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "mealplanner.db"

_STATUS = {NotFound: 404, Conflict: 409, Invalid: 422}


def init_db(db_path: str | Path, modules: list[Module]) -> None:
    conn = connect(db_path)
    try:
        apply_migrations(conn, CORE_OWNER, CORE_MIGRATIONS)
        for mod in modules:
            apply_migrations(conn, mod.name, mod.migrations)
    finally:
        conn.close()


def create_app(db_path: str | Path | None = None) -> FastAPI:
    db_path = db_path or os.environ.get("MEALPLANNER_DB") or DEFAULT_DB_PATH
    modules = discover_modules(mealplanner.modules)
    init_db(db_path, modules)

    app = FastAPI(title="Meal Planner")
    app.state.db_path = str(db_path)
    app.state.modules = modules

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=_STATUS.get(type(exc), 400), content={"detail": str(exc)})

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "modules": [m.name for m in modules]}

    for mod in modules:
        if mod.router is not None:
            app.include_router(mod.router, prefix="/api")
    return app

