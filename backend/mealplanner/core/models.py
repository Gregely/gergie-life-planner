"""Core data models shared by every module (and, later, the AI tool layer).

All quantities are in the ingredient's base unit (g, ml or count).
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Unit = Literal["g", "ml", "count"]
Slot = Literal["breakfast", "lunch", "dinner"]
SLOTS: tuple[Slot, ...] = ("breakfast", "lunch", "dinner")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Ingredients ---------------------------------------------------------------

class IngredientIn(_In):
    name: str = Field(min_length=1, max_length=200)
    unit: Unit
    price_per_unit: float | None = Field(default=None, ge=0, description="Price per base unit (per g, ml or item)")
    is_staple: bool = False
    category: str | None = Field(default=None, max_length=100, description="Optional grouping, e.g. 'produce'")

    @field_validator("category")
    @classmethod
    def _blank_category_is_none(cls, v: str | None) -> str | None:
        return v or None


class Ingredient(IngredientIn):
    id: int


# --- Recipes -------------------------------------------------------------------

class RecipeItemIn(_In):
    ingredient_id: int
    quantity: float = Field(gt=0, description="Amount in the ingredient's base unit")


class RecipeIn(_In):
    name: str = Field(min_length=1, max_length=200)
    servings: int = Field(gt=0)
    items: list[RecipeItemIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_ingredients(self) -> "RecipeIn":
        seen: set[int] = set()
        for item in self.items:
            if item.ingredient_id in seen:
                raise ValueError(f"ingredient {item.ingredient_id} is listed more than once")
            seen.add(item.ingredient_id)
        return self


class RecipeItem(BaseModel):
    ingredient_id: int
    name: str
    unit: Unit
    quantity: float


class Recipe(BaseModel):
    id: int
    name: str
    servings: int
    items: list[RecipeItem]


class RecipeSummary(BaseModel):
    id: int
    name: str
    servings: int


# --- Pantry --------------------------------------------------------------------

class PantrySet(_In):
    quantity: float = Field(ge=0)


class PantryItem(BaseModel):
    ingredient_id: int
    name: str
    unit: Unit
    quantity: float


# --- Plan ----------------------------------------------------------------------

class PlanEntryIn(_In):
    date: dt.date
    slot: Slot
    recipe_id: int
    servings_multiplier: float = Field(default=1.0, gt=0, description="Scales every recipe quantity")


class PlanEntry(BaseModel):
    id: int
    date: dt.date
    slot: Slot
    recipe_id: int
    recipe_name: str
    servings_multiplier: float
    eaten_at: str | None
