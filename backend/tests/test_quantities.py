from mealplanner.core.quantities import aggregate_requirements, clean, pantry_lookup


def test_scales_by_multiplier():
    assert aggregate_requirements([(1, 200, 1.5)]) == {1: 300}


def test_sums_same_ingredient_across_meals():
    rows = [(1, 100, 1), (2, 50, 1), (1, 100, 2), (1, 30, 0.5)]
    assert aggregate_requirements(rows) == {1: 315, 2: 50}


def test_empty_plan_needs_nothing():
    assert aggregate_requirements([]) == {}


def test_float_noise_is_cleaned():
    # 0.1 * 3 == 0.30000000000000004 in binary floating point
    assert aggregate_requirements([(1, 0.1, 1), (1, 0.1, 1), (1, 0.1, 1)]) == {1: 0.3}
    assert clean(1e-12) == 0.0


def test_missing_pantry_row_is_zero():
    assert pantry_lookup({}, 5) == 0.0
    assert pantry_lookup({5: 12.0}, 5) == 12.0
