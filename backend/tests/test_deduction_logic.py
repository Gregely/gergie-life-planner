from mealplanner.modules.eaten.logic import compute_deductions


def by_id(lines):
    return {l.ingredient_id: l for l in lines}


def test_enough_in_pantry_deducts_fully():
    line = by_id(compute_deductions({1: 200}, {1: 500}))[1]
    assert (line.deducted, line.remaining, line.shortfall) == (200, 300, 0)


def test_exact_amount_goes_to_zero_without_shortfall():
    line = by_id(compute_deductions({1: 500}, {1: 500}))[1]
    assert (line.deducted, line.remaining, line.shortfall) == (500, 0, 0)


def test_insufficient_pantry_clamps_at_zero_and_reports_shortfall():
    line = by_id(compute_deductions({1: 500}, {1: 200}))[1]
    assert (line.available, line.deducted, line.remaining, line.shortfall) == (200, 200, 0, 300)


def test_missing_pantry_item_is_entire_shortfall():
    line = by_id(compute_deductions({1: 3}, {}))[1]
    assert (line.available, line.deducted, line.remaining, line.shortfall) == (0, 0, 0, 3)


def test_mixed_ingredients_handled_independently():
    lines = by_id(compute_deductions({1: 100, 2: 100, 3: 100}, {1: 1000, 2: 50}))
    assert [lines[i].shortfall for i in (1, 2, 3)] == [0, 50, 100]
    assert [lines[i].remaining for i in (1, 2, 3)] == [900, 0, 0]


def test_remaining_is_never_negative():
    for need, have in [(1, 0), (10, 9.999), (0.3, 0.1 + 0.2), (1e9, 1)]:
        line = compute_deductions({1: need}, {1: have})[0]
        assert line.remaining >= 0
        assert line.shortfall >= 0
        assert abs(line.deducted + line.shortfall - line.required) < 1e-6


def test_float_noise_is_not_reported_as_shortfall():
    line = compute_deductions({1: 0.1 + 0.2}, {1: 0.3})[0]
    assert line.shortfall == 0 and line.remaining == 0


def test_ingredients_not_needed_are_not_touched():
    assert [l.ingredient_id for l in compute_deductions({1: 5}, {1: 10, 2: 10})] == [1]
