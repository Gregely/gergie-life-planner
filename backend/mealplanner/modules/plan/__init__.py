"""Meal plan: one recipe per (date, slot) and the number of portions eaten."""

import datetime as dt

from fastapi import APIRouter

from mealplanner.core import repo
from mealplanner.core.deps import Conn
from mealplanner.core.models import PlanEntry, PlanEntryIn
from mealplanner.core.module import Module

router = APIRouter(prefix="/plan", tags=["plan"])


@router.get("")
def list_plan(start: dt.date, end: dt.date, conn: Conn) -> list[PlanEntry]:
    return repo.list_plan(conn, start, end)


@router.post("", status_code=201)
def create_plan_entry(data: PlanEntryIn, conn: Conn) -> PlanEntry:
    return repo.create_plan_entry(conn, data)


@router.get("/{plan_id}")
def get_plan_entry(plan_id: int, conn: Conn) -> PlanEntry:
    return repo.get_plan_entry(conn, plan_id)


@router.put("/{plan_id}")
def update_plan_entry(plan_id: int, data: PlanEntryIn, conn: Conn) -> PlanEntry:
    return repo.update_plan_entry(conn, plan_id, data)


@router.delete("/{plan_id}", status_code=204)
def delete_plan_entry(plan_id: int, conn: Conn) -> None:
    repo.delete_plan_entry(conn, plan_id)


module = Module(name="plan", router=router, description="Weekly meal plan")
