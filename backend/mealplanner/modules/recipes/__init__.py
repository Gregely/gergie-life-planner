"""Recipes CRUD. PUT replaces the whole ingredient list."""

from fastapi import APIRouter

from mealplanner.core import repo
from mealplanner.core.deps import Conn
from mealplanner.core.models import Recipe, RecipeIn, RecipeSummary
from mealplanner.core.module import Module

router = APIRouter(prefix="/recipes", tags=["recipes"])


@router.get("")
def list_recipes(conn: Conn) -> list[RecipeSummary]:
    return repo.list_recipes(conn)


@router.post("", status_code=201)
def create_recipe(data: RecipeIn, conn: Conn) -> Recipe:
    return repo.create_recipe(conn, data)


@router.get("/{recipe_id}")
def get_recipe(recipe_id: int, conn: Conn) -> Recipe:
    return repo.get_recipe(conn, recipe_id)


@router.put("/{recipe_id}")
def update_recipe(recipe_id: int, data: RecipeIn, conn: Conn) -> Recipe:
    return repo.update_recipe(conn, recipe_id, data)


@router.delete("/{recipe_id}", status_code=204)
def delete_recipe(recipe_id: int, conn: Conn) -> None:
    repo.delete_recipe(conn, recipe_id)


module = Module(name="recipes", router=router, description="Recipes and their ingredient lists")
