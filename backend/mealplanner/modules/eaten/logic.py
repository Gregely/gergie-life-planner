"""Pantry deduction maths for mark-as-eaten. Pure functions: no database, no HTTP.

Each required ingredient is taken from the pantry, but the pantry never goes
below zero. Whatever could not be covered is the shortfall, which is reported.
"""

from __future__ import annotations

from typing import Mapping

from pydantic import BaseModel

from mealplanner.core.quantities import EPSILON, clean, pantry_lookup


class Deduction(BaseModel):
    ingredient_id: int
    required: float  # what the meal used (recipe quantity x portions / servings)
    available: float  # pantry quantity before eating (0 if not in the pantry)
    deducted: float  # min(required, available)
    remaining: float  # pantry quantity afterwards, never negative
    shortfall: float  # required - deducted; > 0 means the pantry would have gone negative


def compute_deductions(required: Mapping[int, float], pantry: Mapping[int, float]) -> list[Deduction]:
    lines: list[Deduction] = []
    for ingredient_id, need in sorted(required.items()):
        have = pantry_lookup(pantry, ingredient_id)
        taken = min(need, have)
        shortfall = need - taken
        lines.append(
            Deduction(
                ingredient_id=ingredient_id,
                required=clean(need),
                available=clean(have),
                deducted=clean(taken),
                remaining=clean(max(have - taken, 0.0)),
                shortfall=clean(shortfall) if shortfall > EPSILON else 0.0,
            )
        )
    return lines
