"""Pantry: what you have, keyed by ingredient. PUT sets an absolute quantity."""

from fastapi import APIRouter

from mealplanner.core import repo
from mealplanner.core.deps import Conn
from mealplanner.core.models import PantryItem, PantrySet
from mealplanner.core.module import Module

router = APIRouter(prefix="/pantry", tags=["pantry"])


@router.get("")
def list_pantry(conn: Conn) -> list[PantryItem]:
    return repo.list_pantry(conn)


@router.get("/{ingredient_id}")
def get_pantry_item(ingredient_id: int, conn: Conn) -> PantryItem:
    return repo.get_pantry_item(conn, ingredient_id)


@router.put("/{ingredient_id}")
def set_pantry_item(ingredient_id: int, data: PantrySet, conn: Conn) -> PantryItem:
    return repo.set_pantry_quantity(conn, ingredient_id, data.quantity)


@router.delete("/{ingredient_id}", status_code=204)
def delete_pantry_item(ingredient_id: int, conn: Conn) -> None:
    repo.delete_pantry_item(conn, ingredient_id)


module = Module(name="pantry", router=router, description="Pantry stock levels")
