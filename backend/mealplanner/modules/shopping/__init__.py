"""Shopping list for a date range: what the plan needs that the pantry doesn't have."""

from __future__ import annotations

import datetime as dt
import sqlite3

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mealplanner.core import repo
from mealplanner.core.db import transaction
from mealplanner.core.deps import Conn
from mealplanner.core.errors import Conflict, Invalid
from mealplanner.core.models import Unit
from mealplanner.core.module import Module
from mealplanner.core.quantities import aggregate_requirements
from mealplanner.modules.shopping.logic import (
    Purchase,
    ShoppingList,
    add_purchases,
    build_shopping_list,
    resolve_purchase_quantities,
)


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


# --- "Bought": add checked shopping-list items to the pantry --------------------


class BoughtItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ingredient_id: int
    quantity: float | None = Field(
        default=None, gt=0, description="Base units bought; omit to use the list's to-buy amount"
    )


class BoughtIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: dt.date  # the shopping list's date range, used for default amounts
    end: dt.date
    items: list[BoughtItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "BoughtIn":
        ids = [item.ingredient_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("an ingredient is listed more than once")
        return self


class PurchaseReport(Purchase):
    name: str
    unit: Unit


class BoughtResult(BaseModel):
    added: list[PurchaseReport]
    shopping_list: ShoppingListResponse  # recomputed after the pantry update


def record_bought(conn: sqlite3.Connection, data: BoughtIn) -> BoughtResult:
    """Add bought items to the pantry in one step. All or nothing."""
    ids = [item.ingredient_id for item in data.items]
    with transaction(conn):
        ingredients = repo.ingredients_by_id(conn, ids)
        missing = sorted(set(ids) - set(ingredients))
        if missing:
            raise Invalid(f"unknown ingredient id(s): {', '.join(map(str, missing))}")

        current = shopping_list(conn, data.start, data.end)
        to_buy = {item.ingredient_id: item.to_buy for item in current.items}
        try:
            bought = resolve_purchase_quantities({i.ingredient_id: i.quantity for i in data.items}, to_buy)
        except KeyError as exc:
            name = ingredients[exc.args[0]].name
            raise Conflict(f"{name} is not on the shopping list for these dates; enter the amount bought") from None

        purchases = add_purchases(bought, repo.pantry_quantities(conn))
        for purchase in purchases:
            repo.set_pantry_quantity(conn, purchase.ingredient_id, purchase.pantry_after)

    return BoughtResult(
        added=[
            PurchaseReport(**p.model_dump(), name=ingredients[p.ingredient_id].name, unit=ingredients[p.ingredient_id].unit)
            for p in purchases
        ],
        shopping_list=shopping_list(conn, data.start, data.end),
    )


router = APIRouter(prefix="/shopping-list", tags=["shopping"])


@router.get("")
def get_shopping_list(start: dt.date, end: dt.date, conn: Conn) -> ShoppingListResponse:
    return shopping_list(conn, start, end)


@router.post("/bought")
def post_bought(data: BoughtIn, conn: Conn) -> BoughtResult:
    return record_bought(conn, data)


module = Module(name="shopping", router=router, description="Shopping list from plan minus pantry")
