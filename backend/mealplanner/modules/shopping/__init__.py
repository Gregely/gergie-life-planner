"""Shopping list for a date range: what the plan needs that the pantry doesn't have."""

from __future__ import annotations

import datetime as dt
import sqlite3

from fastapi import APIRouter

from mealplanner.core import repo
from mealplanner.core.deps import Conn
from mealplanner.core.module import Module
from mealplanner.core.quantities import aggregate_requirements
from mealplanner.modules.shopping.logic import ShoppingList, build_shopping_list


class ShoppingListResponse(ShoppingList):
    start: dt.date
    end: dt.date


def shopping_list(conn: sqlite3.Connection, start: dt.date, end: dt.date) -> ShoppingListResponse:
    """Shopping list for planned, not-yet-eaten meals between ``start`` and ``end`` inclusive."""
    needed = aggregate_requirements(repo.requirement_rows(conn, start=start, end=end))
    result = build_shopping_list(
        needed,
        repo.pantry_quantities(conn),
        repo.ingredients_by_id(conn, list(needed)),
    )
    return ShoppingListResponse(start=start, end=end, **result.model_dump())


router = APIRouter(prefix="/shopping-list", tags=["shopping"])


@router.get("")
def get_shopping_list(start: dt.date, end: dt.date, conn: Conn) -> ShoppingListResponse:
    return shopping_list(conn, start, end)


module = Module(name="shopping", router=router, description="Shopping list from plan minus pantry")
