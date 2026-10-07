import pytest

from mealplanner.core.quantities import aggregate_requirements, clean, pantry_lookup, portion_quantity


@pytest.mark.parametrize(
    "quantity, portions, servings, expected",
    [
        (500, 4, 4, 500),  # all the portions = one batch
        (500, 1, 4, 125),  # one portion = a quarter
        (500, 2, 4, 250),  # two portions = half a batch
        (500, 8, 4, 1000),  # more portions than a batch makes
        (200, 1.5, 1, 300),
    ],
)
def test_portion_quantity(quantity, portions, servings, expected):
    assert portion_quantity(quantity, portions, servings) == expected


def test_four_single_portions_of_four_serving_recipe_is_one_batch():
    rows = [(1, 500, 1, 4)] * 4
    assert aggregate_requirements(rows) == {1: 500}


def test_two_portions_of_four_serving_recipe_is_half_a_batch():
    assert aggregate_requirements([(1, 500, 2, 4)]) == {1: 250}


def test_thirds_sum_back_to_exactly_one_batch():
    assert aggregate_requirements([(1, 100, 1, 3)] * 3) == {1: 100}
    assert aggregate_requirements([(1, 100, 1, 7)] * 7) == {1: 100}


def test_sums_same_ingredient_across_meals():
    rows = [(1, 100, 2, 2), (2, 50, 1, 1), (1, 100, 4, 2), (1, 30, 1, 2)]
    assert aggregate_requirements(rows) == {1: 315, 2: 50}


def test_empty_plan_needs_nothing():
    assert aggregate_requirements([]) == {}


def test_float_noise_is_cleaned():
    # 0.1 * 3 == 0.30000000000000004 in binary floating point
    assert aggregate_requirements([(1, 0.1, 1, 1), (1, 0.1, 1, 1), (1, 0.1, 1, 1)]) == {1: 0.3}
    assert clean(1e-12) == 0.0


def test_missing_pantry_row_is_zero():
    assert pantry_lookup({}, 5) == 0.0
    assert pantry_lookup({5: 12.0}, 5) == 12.0
