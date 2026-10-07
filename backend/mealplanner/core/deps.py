"""FastAPI dependencies shared by module routers."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Iterator

from fastapi import Depends, Request

from mealplanner.core.db import connect


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """One connection per request, closed afterwards."""
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
