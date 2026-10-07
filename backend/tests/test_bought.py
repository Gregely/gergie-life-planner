"""The "bought" flow: add checked shopping-list items to the pantry in one step."""

import pytest

from mealplanner.modules.shopping.logic import add_purchases, resolve_purchase_quantities

WEEK = ("2026-10-05", "2026-10-11")


# --- pure logic ---


def test_resolve_defaults_to_list_amount_and_keeps_edits():
    assert resolve_purchase_quantities({1: None, 2: 50}, {1: 300, 2: 100}) == {1: 300, 2: 50}


def test_resolve_without_amount_for_item_not_on_list():
    with pytest.raises(KeyError):
        resolve_purchase_quantities({3: None}, {1: 300})


def test_resolve_explicit_amount_for_item_not_on_list_is_fine():
    assert resolve_purchase_quantities({3: 2}, {}) == {3: 2}


def test_add_purchases_on_top_of_existing_and_missing_pantry():
    lines = {p.ingredient_id: p for p in add_purchases({1: 300, 2: 6}, {1: 200})}
    assert (lines[1].pantry_before, lines[1].added, lines[1].pantry_after) == (200, 300, 500)
    assert (lines[2].pantry_before, lines[2].added, lines[2].pantry_after) == (0, 6, 6)


def test_add_purchases_cleans_float_noise():
    assert add_purchases({1: 0.2}, {1: 0.1})[0].pantry_after == 0.3


# --- API ---


@pytest.fixture
def week_of_chilli(api):
    """A 4-serving chilli on 4 nights (one batch): 500 g mince, 2 onions, 1 tin, salt (staple)."""
    ids = {
        "mince": api.ingredient("Mince", price=0.01),
        "onion": api.ingredient("Onion", unit="count"),
        "tomatoes": api.ingredient("Tomatoes", unit="count"),
        "salt": api.ingredient("Salt", staple=True),
    }
    chilli = api.recipe("Chilli", {ids["mince"]: 500, ids["onion"]: 2, ids["tomatoes"]: 1, ids["salt"]: 5}, servings=4)
    for day in ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"):
        api.plan(day, "dinner", chilli, portions=1)
    return ids


def test_default_amount_is_the_amount_to_buy(api, week_of_chilli):
    mince = week_of_chilli["mince"]
    result = api.bought(*WEEK, {mince: None})
    assert api.pantry() == {mince: 500}
    assert [(a["name"], a["added"], a["pantry_after"]) for a in result["added"]] == [("Mince", 500, 500)]
    assert mince not in api.to_buy(*WEEK)


def test_unchecked_items_stay_on_the_list(api, week_of_chilli):
    ids = week_of_chilli
    result = api.bought(*WEEK, {ids["mince"]: None, ids["onion"]: None})
    remaining = {i["ingredient_id"]: i["to_buy"] for i in result["shopping_list"]["items"]}
    assert remaining == {ids["tomatoes"]: 1}
    assert api.to_buy(*WEEK) == remaining


def test_item_already_partly_in_pantry_adds_on_top(api, week_of_chilli):
    mince = week_of_chilli["mince"]
    api.stock(mince, 200)
    assert api.to_buy(*WEEK)[mince] == 300
    [added] = api.bought(*WEEK, {mince: None})["added"]
    assert (added["pantry_before"], added["added"], added["pantry_after"]) == (200, 300, 500)
    assert api.pantry() == {mince: 500}
    assert mince not in api.to_buy(*WEEK)


def test_partial_buy_leaves_the_rest_on_the_list(api, week_of_chilli):
    mince = week_of_chilli["mince"]
    api.stock(mince, 100)
    api.bought(*WEEK, {mince: 250})  # list wanted 400
    assert api.pantry() == {mince: 350}
    assert api.to_buy(*WEEK)[mince] == 150


def test_buying_more_than_needed_is_kept(api, week_of_chilli):
    onion = week_of_chilli["onion"]
    api.bought(*WEEK, {onion: 5})
    assert api.pantry() == {onion: 5}
    assert onion not in api.to_buy(*WEEK)


def test_mixed_default_and_edited_amounts_in_one_step(api, week_of_chilli):
    ids = week_of_chilli
    api.stock(ids["onion"], 1)
    api.bought(*WEEK, {ids["mince"]: 450, ids["onion"]: None, ids["tomatoes"]: None})
    assert api.pantry() == {ids["mince"]: 450, ids["onion"]: 2, ids["tomatoes"]: 1}
    assert api.to_buy(*WEEK) == {ids["mince"]: 50}


def test_explicit_amount_for_item_not_on_list(api, week_of_chilli):
    salt = week_of_chilli["salt"]  # staples never appear on the list but can still be bought
    api.bought(*WEEK, {salt: 750})
    assert api.pantry() == {salt: 750}


def test_default_for_item_not_on_list_is_rejected_and_nothing_applied(api, client, week_of_chilli):
    ids = week_of_chilli
    resp = client.post("/api/shopping-list/bought", json={
        "start": WEEK[0], "end": WEEK[1],
        "items": [{"ingredient_id": ids["mince"]}, {"ingredient_id": ids["salt"]}],
    })
    assert resp.status_code == 409 and "Salt" in resp.json()["detail"]
    assert api.pantry() == {}


def test_unknown_ingredient_rejects_whole_request(api, client, week_of_chilli):
    resp = client.post("/api/shopping-list/bought", json={
        "start": WEEK[0], "end": WEEK[1],
        "items": [{"ingredient_id": week_of_chilli["mince"]}, {"ingredient_id": 999, "quantity": 1}],
    })
    assert resp.status_code == 422
    assert api.pantry() == {}


def test_default_amount_uses_this_range_only(api, week_of_chilli):
    mince = week_of_chilli["mince"]
    api.bought("2026-10-05", "2026-10-06", {mince: None})  # two portions = half a batch
    assert api.pantry() == {mince: 250}
    assert api.to_buy(*WEEK)[mince] == 250


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"ingredient_id": 1, "quantity": 0}],
        [{"ingredient_id": 1, "quantity": -5}],
        [{"ingredient_id": 1, "quantity": 1}, {"ingredient_id": 1, "quantity": 2}],
        [{"ingredient_id": 1, "quantity": 1, "price": 3}],
    ],
)
def test_validation(api, client, week_of_chilli, items):
    resp = client.post("/api/shopping-list/bought", json={"start": WEEK[0], "end": WEEK[1], "items": items})
    assert resp.status_code == 422
    assert api.pantry() == {}


def test_invalid_range(api, client, week_of_chilli):
    resp = client.post("/api/shopping-list/bought", json={
        "start": WEEK[1], "end": WEEK[0], "items": [{"ingredient_id": week_of_chilli["mince"], "quantity": 1}],
    })
    assert resp.status_code == 422
    assert api.pantry() == {}
