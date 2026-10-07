import pytest

from mealplanner.core.models import Ingredient
from mealplanner.modules.shopping.logic import build_shopping_list


def ing(id, name=None, unit="g", price=None, staple=False, category=None):
    return Ingredient(id=id, name=name or f"ing{id}", unit=unit, price_per_unit=price, is_staple=staple, category=category)


def lines(result):
    return {i.ingredient_id: (i.needed, i.in_pantry, i.to_buy) for i in result.items}


def test_missing_from_pantry_buys_everything():
    result = build_shopping_list({1: 500}, {}, {1: ing(1)})
    assert lines(result) == {1: (500, 0, 500)}


def test_partial_pantry_buys_only_the_shortfall():
    result = build_shopping_list({1: 500}, {1: 200}, {1: ing(1)})
    assert lines(result) == {1: (500, 200, 300)}


@pytest.mark.parametrize("have", [500, 501, 10_000])
def test_pantry_covers_need_so_nothing_to_buy(have):
    assert build_shopping_list({1: 500}, {1: have}, {1: ing(1)}).items == []


def test_zero_pantry_row_behaves_like_missing():
    assert lines(build_shopping_list({1: 4}, {1: 0}, {1: ing(1, unit="count")})) == {1: (4, 0, 4)}


def test_staples_never_listed_even_if_absent_from_pantry():
    ingredients = {1: ing(1, "Salt", staple=True), 2: ing(2, "Oil", unit="ml", staple=True), 3: ing(3, "Rice")}
    result = build_shopping_list({1: 10, 2: 30, 3: 200}, {}, ingredients)
    assert [i.name for i in result.items] == ["Rice"]


def test_float_noise_does_not_create_tiny_purchases():
    needed = {1: 0.1 + 0.2}  # 0.30000000000000004
    assert build_shopping_list(needed, {1: 0.3}, {1: ing(1)}).items == []


def test_costs_and_totals():
    ingredients = {1: ing(1, "Flour", price=0.002), 2: ing(2, "Eggs", unit="count", price=0.25), 3: ing(3, "Herbs")}
    result = build_shopping_list({1: 1000, 2: 6, 3: 20}, {1: 250, 2: 2}, ingredients)
    costs = {i.name: i.estimated_cost for i in result.items}
    assert costs == {"Flour": 1.5, "Eggs": 1.0, "Herbs": None}
    assert result.estimated_total == 2.5
    assert result.unpriced_items == 1


def test_sorted_by_category_then_name_uncategorised_last():
    ingredients = {
        1: ing(1, "banana", category="Produce"),
        2: ing(2, "Apple", category="produce"),
        3: ing(3, "Milk", category="Dairy"),
        4: ing(4, "Mystery"),
    }
    result = build_shopping_list({1: 1, 2: 1, 3: 1, 4: 1}, {}, ingredients)
    assert [i.name for i in result.items] == ["Milk", "Apple", "banana", "Mystery"]


def test_empty_plan_gives_empty_list():
    result = build_shopping_list({}, {1: 100}, {})
    assert result.items == [] and result.estimated_total == 0 and result.unpriced_items == 0
