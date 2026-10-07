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


def aggregate_requirements(rows: Iterable[tuple[int, float, float]]) -> dict[int, float]:
    """Total each ingredient across meals.

    ``rows`` are ``(ingredient_id, recipe_quantity, servings_multiplier)``, one
    per recipe item per planned meal. The same ingredient appearing in several
    meals is summed; each line is scaled by its meal's multiplier.
    """
    totals: dict[int, float] = defaultdict(float)
    for ingredient_id, quantity, multiplier in rows:
        totals[ingredient_id] += quantity * multiplier
    return {ingredient_id: clean(total) for ingredient_id, total in totals.items()}


def pantry_lookup(pantry: Mapping[int, float], ingredient_id: int) -> float:
    """Pantry quantity for an ingredient; a missing pantry row means zero."""
    return max(pantry.get(ingredient_id, 0.0), 0.0)
