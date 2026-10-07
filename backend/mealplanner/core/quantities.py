"""Shared quantity maths used by the shopping list and pantry deduction."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

# Quantities are floats (e.g. 0.5 * 150 g). Anything within EPSILON of zero is
# treated as zero so float noise never produces "buy 0.0000001 g of flour".
EPSILON = 1e-6
_DECIMALS = 6


def clean(value: float) -> float:
    """Round away float noise; snap near-zero values to exactly 0."""
    if abs(value) < EPSILON:
        return 0.0
    return round(value, _DECIMALS)


def portion_quantity(recipe_quantity: float, portions: float, recipe_servings: int) -> float:
    """Amount of an ingredient needed for ``portions`` of a recipe that makes ``recipe_servings``.

    E.g. 500 g in a 4-serving recipe: 1 portion needs 125 g, 4 portions 500 g.
    """
    return recipe_quantity * portions / recipe_servings


def aggregate_requirements(rows: Iterable[tuple[int, float, float, int]]) -> dict[int, float]:
    """Total each ingredient across meals.

    ``rows`` are ``(ingredient_id, recipe_quantity, portions, recipe_servings)``,
    one per recipe item per planned meal. The same ingredient appearing in
    several meals is summed after each line is scaled to its meal's portions.
    """
    totals: dict[int, float] = defaultdict(float)
    for ingredient_id, quantity, portions, servings in rows:
        totals[ingredient_id] += portion_quantity(quantity, portions, servings)
    return {ingredient_id: clean(total) for ingredient_id, total in totals.items()}


def pantry_lookup(pantry: Mapping[int, float], ingredient_id: int) -> float:
    """Pantry quantity for an ingredient; a missing pantry row means zero."""
    return max(pantry.get(ingredient_id, 0.0), 0.0)
