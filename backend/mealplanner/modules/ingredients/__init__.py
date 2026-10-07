"""Ingredients CRUD."""

from fastapi import APIRouter

from mealplanner.core import repo
from mealplanner.core.deps import Conn
from mealplanner.core.models import Ingredient, IngredientIn
from mealplanner.core.module import Module

router = APIRouter(prefix="/ingredients", tags=["ingredients"])


@router.get("")
def list_ingredients(conn: Conn) -> list[Ingredient]:
    return repo.list_ingredients(conn)


@router.post("", status_code=201)
def create_ingredient(data: IngredientIn, conn: Conn) -> Ingredient:
    return repo.create_ingredient(conn, data)


@router.get("/{ingredient_id}")
def get_ingredient(ingredient_id: int, conn: Conn) -> Ingredient:
    return repo.get_ingredient(conn, ingredient_id)


@router.put("/{ingredient_id}")
def update_ingredient(ingredient_id: int, data: IngredientIn, conn: Conn) -> Ingredient:
    return repo.update_ingredient(conn, ingredient_id, data)


@router.delete("/{ingredient_id}", status_code=204)
def delete_ingredient(ingredient_id: int, conn: Conn) -> None:
    repo.delete_ingredient(conn, ingredient_id)


module = Module(name="ingredients", router=router, description="Ingredient catalogue")
