"""Shopping list maths. Pure functions: no database, no HTTP.

shopping list = (sum of plan requirements, scaled) - pantry - staples,
keeping only positive shortfalls.
"""

from __future__ import annotations

from typing import Mapping

from pydantic import BaseModel

from mealplanner.core.models import Ingredient, Unit
from mealplanner.core.quantities import EPSILON, clean, pantry_lookup


class ShoppingItem(BaseModel):
    ingredient_id: int
    name: str
    unit: Unit
    category: str | None
    needed: float  # total required by the plan in the range
    in_pantry: float  # what the pantry already holds
    to_buy: float  # needed - in_pantry, always > 0
    price_per_unit: float | None
    estimated_cost: float | None  # None when the ingredient has no price


class ShoppingList(BaseModel):
    items: list[ShoppingItem]
    estimated_total: float  # sum of the items that have a price
    unpriced_items: int  # how many items are missing from estimated_total


def build_shopping_list(
    needed: Mapping[int, float],
    pantry: Mapping[int, float],
    ingredients: Mapping[int, Ingredient],
) -> ShoppingList:
    items: list[ShoppingItem] = []
    for ingredient_id, required in needed.items():
        ingredient = ingredients[ingredient_id]
        if ingredient.is_staple:
            continue
        have = pantry_lookup(pantry, ingredient_id)
        shortfall = required - have
        if shortfall <= EPSILON:
            continue
        cost = None if ingredient.price_per_unit is None else round(shortfall * ingredient.price_per_unit, 2)
        items.append(
            ShoppingItem(
                ingredient_id=ingredient_id,
                name=ingredient.name,
                unit=ingredient.unit,
                category=ingredient.category,
                needed=clean(required),
                in_pantry=clean(have),
                to_buy=clean(shortfall),
                price_per_unit=ingredient.price_per_unit,
                estimated_cost=cost,
            )
        )
    items.sort(key=lambda i: ((i.category or "~").lower(), i.name.lower()))
    priced = [i.estimated_cost for i in items if i.estimated_cost is not None]
    return ShoppingList(
        items=items,
        estimated_total=round(sum(priced), 2),
        unpriced_items=len(items) - len(priced),
    )


class Purchase(BaseModel):
    ingredient_id: int
    added: float  # quantity added to the pantry
    pantry_before: float  # 0 if the ingredient was not in the pantry
    pantry_after: float


def resolve_purchase_quantities(
    requested: Mapping[int, float | None],
    to_buy: Mapping[int, float],
) -> dict[int, float]:
    """Quantity bought per ingredient: the edited amount if given, else the list's to-buy amount.

    Raises ``KeyError`` (with the ingredient id) when no amount is given for an
    ingredient that is not on the shopping list, as there is nothing to default to.
    """
    resolved: dict[int, float] = {}
    for ingredient_id, quantity in requested.items():
        if quantity is None:
            if ingredient_id not in to_buy:
                raise KeyError(ingredient_id)
            quantity = to_buy[ingredient_id]
        resolved[ingredient_id] = quantity
    return resolved


def add_purchases(bought: Mapping[int, float], pantry: Mapping[int, float]) -> list[Purchase]:
    """Add bought quantities on top of what is already in the pantry."""
    purchases = []
    for ingredient_id, quantity in sorted(bought.items()):
        before = pantry_lookup(pantry, ingredient_id)
        purchases.append(
            Purchase(
                ingredient_id=ingredient_id,
                added=clean(quantity),
                pantry_before=clean(before),
                pantry_after=clean(before + quantity),
            )
        )
    return purchases
