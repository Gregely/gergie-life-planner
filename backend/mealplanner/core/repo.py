"""Data access for the core entities.

Routers stay thin and call these functions; later modules (e.g. the AI tool
layer) reuse them, so validation and integrity rules live in one place.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from mealplanner.core.db import transaction
from mealplanner.core.errors import Conflict, Invalid, NotFound
from mealplanner.core.models import (
    Ingredient,
    IngredientIn,
    PantryItem,
    PlanEntry,
    PlanEntryIn,
    Recipe,
    RecipeIn,
    RecipeItem,
    RecipeSummary,
)

# --- Ingredients ---------------------------------------------------------------


def _ingredient(row: sqlite3.Row) -> Ingredient:
    return Ingredient(
        id=row["id"],
        name=row["name"],
        unit=row["unit"],
        price_per_unit=row["price_per_unit"],
        is_staple=bool(row["is_staple"]),
        category=row["category"],
    )


def list_ingredients(conn: sqlite3.Connection) -> list[Ingredient]:
    rows = conn.execute("SELECT * FROM ingredients ORDER BY name COLLATE NOCASE")
    return [_ingredient(r) for r in rows]


def get_ingredient(conn: sqlite3.Connection, ingredient_id: int) -> Ingredient:
    row = conn.execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
    if row is None:
        raise NotFound(f"ingredient {ingredient_id} not found")
    return _ingredient(row)


def ingredients_by_id(conn: sqlite3.Connection, ids: list[int] | None = None) -> dict[int, Ingredient]:
    if ids is None:
        return {i.id: i for i in list_ingredients(conn)}
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT * FROM ingredients WHERE id IN ({marks})", ids)
    return {r["id"]: _ingredient(r) for r in rows}


def _ingredient_params(data: IngredientIn) -> tuple:
    return (data.name, data.unit, data.price_per_unit, int(data.is_staple), data.category)


def create_ingredient(conn: sqlite3.Connection, data: IngredientIn) -> Ingredient:
    try:
        cur = conn.execute(
            "INSERT INTO ingredients (name, unit, price_per_unit, is_staple, category) VALUES (?, ?, ?, ?, ?)",
            _ingredient_params(data),
        )
    except sqlite3.IntegrityError:
        raise Conflict(f"an ingredient named {data.name!r} already exists") from None
    return get_ingredient(conn, cur.lastrowid)


def update_ingredient(conn: sqlite3.Connection, ingredient_id: int, data: IngredientIn) -> Ingredient:
    get_ingredient(conn, ingredient_id)
    try:
        conn.execute(
            "UPDATE ingredients SET name = ?, unit = ?, price_per_unit = ?, is_staple = ?, category = ? WHERE id = ?",
            (*_ingredient_params(data), ingredient_id),
        )
    except sqlite3.IntegrityError:
        raise Conflict(f"an ingredient named {data.name!r} already exists") from None
    return get_ingredient(conn, ingredient_id)


def delete_ingredient(conn: sqlite3.Connection, ingredient_id: int) -> None:
    get_ingredient(conn, ingredient_id)
    used = conn.execute(
        "SELECT r.name FROM recipe_items ri JOIN recipes r ON r.id = ri.recipe_id WHERE ri.ingredient_id = ? ORDER BY r.name",
        (ingredient_id,),
    ).fetchall()
    if used:
        names = ", ".join(r["name"] for r in used)
        raise Conflict(f"ingredient is used by recipe(s): {names}")
    conn.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))


# --- Recipes -------------------------------------------------------------------


def list_recipes(conn: sqlite3.Connection) -> list[RecipeSummary]:
    rows = conn.execute("SELECT id, name, servings FROM recipes ORDER BY name COLLATE NOCASE")
    return [RecipeSummary(id=r["id"], name=r["name"], servings=r["servings"]) for r in rows]


def get_recipe(conn: sqlite3.Connection, recipe_id: int) -> Recipe:
    row = conn.execute("SELECT id, name, servings FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if row is None:
        raise NotFound(f"recipe {recipe_id} not found")
    items = conn.execute(
        """
        SELECT ri.ingredient_id, i.name, i.unit, ri.quantity
        FROM recipe_items ri JOIN ingredients i ON i.id = ri.ingredient_id
        WHERE ri.recipe_id = ?
        ORDER BY i.name COLLATE NOCASE
        """,
        (recipe_id,),
    )
    return Recipe(
        id=row["id"],
        name=row["name"],
        servings=row["servings"],
        items=[RecipeItem(**dict(i)) for i in items],
    )


def _check_recipe_ingredients(conn: sqlite3.Connection, data: RecipeIn) -> None:
    ids = [item.ingredient_id for item in data.items]
    missing = sorted(set(ids) - set(ingredients_by_id(conn, ids)))
    if missing:
        raise Invalid(f"unknown ingredient id(s): {', '.join(map(str, missing))}")


def _write_recipe_items(conn: sqlite3.Connection, recipe_id: int, data: RecipeIn) -> None:
    conn.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
    conn.executemany(
        "INSERT INTO recipe_items (recipe_id, ingredient_id, quantity) VALUES (?, ?, ?)",
        [(recipe_id, item.ingredient_id, item.quantity) for item in data.items],
    )


def create_recipe(conn: sqlite3.Connection, data: RecipeIn) -> Recipe:
    _check_recipe_ingredients(conn, data)
    with transaction(conn):
        cur = conn.execute("INSERT INTO recipes (name, servings) VALUES (?, ?)", (data.name, data.servings))
        _write_recipe_items(conn, cur.lastrowid, data)
    return get_recipe(conn, cur.lastrowid)


def update_recipe(conn: sqlite3.Connection, recipe_id: int, data: RecipeIn) -> Recipe:
    """Replace a recipe's name, servings and full ingredient list."""
    get_recipe(conn, recipe_id)
    _check_recipe_ingredients(conn, data)
    with transaction(conn):
        conn.execute("UPDATE recipes SET name = ?, servings = ? WHERE id = ?", (data.name, data.servings, recipe_id))
        _write_recipe_items(conn, recipe_id, data)
    return get_recipe(conn, recipe_id)


def delete_recipe(conn: sqlite3.Connection, recipe_id: int) -> None:
    get_recipe(conn, recipe_id)
    planned = conn.execute("SELECT COUNT(*) FROM plan WHERE recipe_id = ?", (recipe_id,)).fetchone()[0]
    if planned:
        raise Conflict(f"recipe is used by {planned} planned meal(s); remove them from the plan first")
    conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))


# --- Pantry --------------------------------------------------------------------

_PANTRY_SELECT = """
    SELECT p.ingredient_id, i.name, i.unit, p.quantity
    FROM pantry p JOIN ingredients i ON i.id = p.ingredient_id
"""


def list_pantry(conn: sqlite3.Connection) -> list[PantryItem]:
    rows = conn.execute(_PANTRY_SELECT + " ORDER BY i.name COLLATE NOCASE")
    return [PantryItem(**dict(r)) for r in rows]


def get_pantry_item(conn: sqlite3.Connection, ingredient_id: int) -> PantryItem:
    row = conn.execute(_PANTRY_SELECT + " WHERE p.ingredient_id = ?", (ingredient_id,)).fetchone()
    if row is None:
        raise NotFound(f"ingredient {ingredient_id} is not in the pantry")
    return PantryItem(**dict(row))


def pantry_quantities(conn: sqlite3.Connection) -> dict[int, float]:
    return {r["ingredient_id"]: r["quantity"] for r in conn.execute("SELECT ingredient_id, quantity FROM pantry")}


def set_pantry_quantity(conn: sqlite3.Connection, ingredient_id: int, quantity: float) -> PantryItem:
    if quantity < 0:
        raise Invalid("pantry quantity cannot be negative")
    get_ingredient(conn, ingredient_id)
    conn.execute(
        "INSERT INTO pantry (ingredient_id, quantity) VALUES (?, ?) "
        "ON CONFLICT (ingredient_id) DO UPDATE SET quantity = excluded.quantity",
        (ingredient_id, quantity),
    )
    return get_pantry_item(conn, ingredient_id)


def delete_pantry_item(conn: sqlite3.Connection, ingredient_id: int) -> None:
    get_pantry_item(conn, ingredient_id)
    conn.execute("DELETE FROM pantry WHERE ingredient_id = ?", (ingredient_id,))


# --- Plan ----------------------------------------------------------------------

_PLAN_SELECT = """
    SELECT p.id, p.date, p.slot, p.recipe_id, r.name AS recipe_name, p.portions, p.eaten_at
    FROM plan p JOIN recipes r ON r.id = p.recipe_id
"""
_SLOT_ORDER = "CASE p.slot WHEN 'breakfast' THEN 0 WHEN 'lunch' THEN 1 ELSE 2 END"


def _check_range(start: dt.date, end: dt.date) -> None:
    if end < start:
        raise Invalid("end date must be on or after start date")


def list_plan(conn: sqlite3.Connection, start: dt.date, end: dt.date) -> list[PlanEntry]:
    _check_range(start, end)
    rows = conn.execute(
        _PLAN_SELECT + f" WHERE p.date BETWEEN ? AND ? ORDER BY p.date, {_SLOT_ORDER}",
        (start.isoformat(), end.isoformat()),
    )
    return [PlanEntry(**dict(r)) for r in rows]


def get_plan_entry(conn: sqlite3.Connection, plan_id: int) -> PlanEntry:
    row = conn.execute(_PLAN_SELECT + " WHERE p.id = ?", (plan_id,)).fetchone()
    if row is None:
        raise NotFound(f"plan entry {plan_id} not found")
    return PlanEntry(**dict(row))


def _check_slot_free(conn: sqlite3.Connection, data: PlanEntryIn, ignore_id: int | None = None) -> None:
    row = conn.execute(
        "SELECT id FROM plan WHERE date = ? AND slot = ? AND id IS NOT ?",
        (data.date.isoformat(), data.slot, ignore_id),
    ).fetchone()
    if row is not None:
        raise Conflict(f"{data.slot} on {data.date.isoformat()} is already planned (entry {row['id']})")


def create_plan_entry(conn: sqlite3.Connection, data: PlanEntryIn) -> PlanEntry:
    get_recipe(conn, data.recipe_id)
    _check_slot_free(conn, data)
    cur = conn.execute(
        "INSERT INTO plan (date, slot, recipe_id, portions) VALUES (?, ?, ?, ?)",
        (data.date.isoformat(), data.slot, data.recipe_id, data.portions),
    )
    return get_plan_entry(conn, cur.lastrowid)


def update_plan_entry(conn: sqlite3.Connection, plan_id: int, data: PlanEntryIn) -> PlanEntry:
    get_plan_entry(conn, plan_id)
    get_recipe(conn, data.recipe_id)
    _check_slot_free(conn, data, ignore_id=plan_id)
    conn.execute(
        "UPDATE plan SET date = ?, slot = ?, recipe_id = ?, portions = ? WHERE id = ?",
        (data.date.isoformat(), data.slot, data.recipe_id, data.portions, plan_id),
    )
    return get_plan_entry(conn, plan_id)


def delete_plan_entry(conn: sqlite3.Connection, plan_id: int) -> None:
    get_plan_entry(conn, plan_id)
    conn.execute("DELETE FROM plan WHERE id = ?", (plan_id,))


def requirement_rows(
    conn: sqlite3.Connection,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    plan_ids: list[int] | None = None,
    include_eaten: bool = False,
) -> list[tuple[int, float, float, int]]:
    """Raw ``(ingredient_id, recipe_quantity, portions, recipe_servings)`` rows for planned meals.

    Feed the result to :func:`mealplanner.core.quantities.aggregate_requirements`.
    Filter by date range and/or explicit plan entry ids. Meals already marked
    as eaten are excluded unless ``include_eaten`` is set (their ingredients
    have already left the pantry).
    """
    where, params = [], []
    if start is not None and end is not None:
        _check_range(start, end)
        where.append("p.date BETWEEN ? AND ?")
        params += [start.isoformat(), end.isoformat()]
    if plan_ids is not None:
        if not plan_ids:
            return []
        where.append(f"p.id IN ({','.join('?' * len(plan_ids))})")
        params += plan_ids
    if not include_eaten:
        where.append("p.eaten_at IS NULL")
    sql = (
        "SELECT ri.ingredient_id, ri.quantity, p.portions, r.servings FROM plan p "
        "JOIN recipes r ON r.id = p.recipe_id JOIN recipe_items ri ON ri.recipe_id = p.recipe_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [(r[0], r[1], r[2], r[3]) for r in conn.execute(sql, params)]
