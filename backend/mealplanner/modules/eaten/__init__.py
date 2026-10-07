"""Mark a planned meal as eaten: deduct its ingredients from the pantry.

Owns the ``consumption_log`` table, recording exactly what each meal took from
the pantry (and what it was short of).
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from fastapi import APIRouter
from pydantic import BaseModel

from mealplanner.core import repo
from mealplanner.core.db import Migration, transaction
from mealplanner.core.deps import Conn
from mealplanner.core.errors import Conflict
from mealplanner.core.models import Slot, Unit
from mealplanner.core.module import Module
from mealplanner.core.quantities import aggregate_requirements
from mealplanner.modules.eaten.logic import Deduction, compute_deductions

MIGRATIONS = [
    Migration(
        1,
        """
        CREATE TABLE consumption_log (
            id            INTEGER PRIMARY KEY,
            plan_id       INTEGER NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
            ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
            required      REAL    NOT NULL,
            deducted      REAL    NOT NULL,
            shortfall     REAL    NOT NULL,
            eaten_at      TEXT    NOT NULL
        );
        CREATE INDEX consumption_log_plan ON consumption_log(plan_id);
        """,
    ),
]


class DeductionReport(Deduction):
    name: str
    unit: Unit
    is_staple: bool


class EatenResult(BaseModel):
    plan_id: int
    date: dt.date
    slot: Slot
    recipe_name: str
    eaten_at: str
    deductions: list[DeductionReport]
    shortfalls: list[DeductionReport]  # the subset of deductions with shortfall > 0


def mark_eaten(conn: sqlite3.Connection, plan_id: int) -> EatenResult:
    eaten_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with transaction(conn):
        entry = repo.get_plan_entry(conn, plan_id)
        if entry.eaten_at is not None:
            raise Conflict(f"plan entry {plan_id} was already marked as eaten at {entry.eaten_at}")

        required = aggregate_requirements(repo.requirement_rows(conn, plan_ids=[plan_id], include_eaten=True))
        pantry = repo.pantry_quantities(conn)
        lines = compute_deductions(required, pantry)

        for line in lines:
            # Only touch existing pantry rows; an ingredient you never stocked
            # stays absent rather than appearing with quantity 0.
            if line.ingredient_id in pantry:
                conn.execute(
                    "UPDATE pantry SET quantity = ? WHERE ingredient_id = ?",
                    (line.remaining, line.ingredient_id),
                )
        conn.executemany(
            "INSERT INTO consumption_log (plan_id, ingredient_id, required, deducted, shortfall, eaten_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(plan_id, l.ingredient_id, l.required, l.deducted, l.shortfall, eaten_at) for l in lines],
        )
        conn.execute("UPDATE plan SET eaten_at = ? WHERE id = ?", (eaten_at, plan_id))

    ingredients = repo.ingredients_by_id(conn, list(required))
    reports = [
        DeductionReport(
            **line.model_dump(),
            name=ingredients[line.ingredient_id].name,
            unit=ingredients[line.ingredient_id].unit,
            is_staple=ingredients[line.ingredient_id].is_staple,
        )
        for line in lines
    ]
    return EatenResult(
        plan_id=plan_id,
        date=entry.date,
        slot=entry.slot,
        recipe_name=entry.recipe_name,
        eaten_at=eaten_at,
        deductions=reports,
        shortfalls=[r for r in reports if r.shortfall > 0],
    )


class LogEntry(BaseModel):
    ingredient_id: int
    name: str
    unit: Unit
    required: float
    deducted: float
    shortfall: float
    eaten_at: str


def consumption_for(conn: sqlite3.Connection, plan_id: int) -> list[LogEntry]:
    repo.get_plan_entry(conn, plan_id)
    rows = conn.execute(
        """
        SELECT c.ingredient_id, i.name, i.unit, c.required, c.deducted, c.shortfall, c.eaten_at
        FROM consumption_log c JOIN ingredients i ON i.id = c.ingredient_id
        WHERE c.plan_id = ? ORDER BY i.name COLLATE NOCASE
        """,
        (plan_id,),
    )
    return [LogEntry(**dict(r)) for r in rows]


router = APIRouter(prefix="/plan", tags=["eaten"])


@router.post("/{plan_id}/eaten")
def post_eaten(plan_id: int, conn: Conn) -> EatenResult:
    return mark_eaten(conn, plan_id)


@router.get("/{plan_id}/eaten")
def get_eaten(plan_id: int, conn: Conn) -> list[LogEntry]:
    return consumption_for(conn, plan_id)


module = Module(name="eaten", router=router, migrations=MIGRATIONS, description="Mark meals eaten, deduct pantry")
