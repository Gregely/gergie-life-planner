"""Shopping list end to end: plan (scaled) - pantry - staples, shortfalls only."""

WEEK = ("2026-10-05", "2026-10-11")


def test_empty_plan(api):
    assert api.shopping(*WEEK)["items"] == []


def test_missing_pantry_items_are_bought_in_full(api):
    rice = api.ingredient("Rice")
    api.plan("2026-10-06", "dinner", api.recipe("Rice bowl", {rice: 150}))
    assert api.to_buy(*WEEK) == {rice: 150}


def test_multiple_meals_using_same_ingredient_are_summed_then_pantry_subtracted_once(api):
    onion = api.ingredient("Onion", unit="count")
    curry = api.recipe("Curry", {onion: 2})
    soup = api.recipe("Soup", {onion: 1})
    api.plan("2026-10-05", "dinner", curry)
    api.plan("2026-10-06", "lunch", soup)
    api.plan("2026-10-07", "dinner", curry)
    api.stock(onion, 3)
    item = api.shopping(*WEEK)["items"][0]
    assert (item["needed"], item["in_pantry"], item["to_buy"]) == (5, 3, 2)


def test_servings_multiplier_scales_quantities(api):
    pasta = api.ingredient("Pasta")
    recipe = api.recipe("Pasta", {pasta: 100})
    api.plan("2026-10-05", "dinner", recipe, multiplier=2.5)
    api.plan("2026-10-06", "lunch", recipe, multiplier=0.5)
    api.stock(pasta, 50)
    assert api.to_buy(*WEEK) == {pasta: 250 + 50 - 50}


def test_staples_never_appear(api):
    salt = api.ingredient("Salt", staple=True)
    oil = api.ingredient("Olive oil", unit="ml", staple=True)
    eggs = api.ingredient("Eggs", unit="count")
    api.plan("2026-10-05", "breakfast", api.recipe("Fried eggs", {salt: 2, oil: 15, eggs: 2}))
    assert api.to_buy(*WEEK) == {eggs: 2}


def test_fully_stocked_ingredients_are_omitted(api):
    milk = api.ingredient("Milk", unit="ml")
    oats = api.ingredient("Oats")
    api.plan("2026-10-05", "breakfast", api.recipe("Porridge", {milk: 250, oats: 50}))
    api.stock(milk, 1000)
    api.stock(oats, 20)
    assert api.to_buy(*WEEK) == {oats: 30}


def test_only_meals_inside_the_inclusive_date_range_count(api):
    bread = api.ingredient("Bread", unit="count")
    toast = api.recipe("Toast", {bread: 1})
    for day in ("2026-10-04", "2026-10-05", "2026-10-11", "2026-10-12"):
        api.plan(day, "breakfast", toast)
    assert api.to_buy(*WEEK) == {bread: 2}
    assert api.to_buy("2026-10-05", "2026-10-05") == {bread: 1}


def test_eaten_meals_are_excluded(api):
    beans = api.ingredient("Beans")
    recipe = api.recipe("Beans", {beans: 100})
    first = api.plan("2026-10-05", "lunch", recipe)
    api.plan("2026-10-06", "lunch", recipe)
    api.stock(beans, 100)
    api.eat(first)  # uses up the 100 g in the pantry
    assert api.to_buy(*WEEK) == {beans: 100}


def test_prices_and_totals(api):
    flour = api.ingredient("Flour", price=0.002, category="Baking")
    yeast = api.ingredient("Yeast")
    api.plan("2026-10-05", "dinner", api.recipe("Bread", {flour: 500, yeast: 7}))
    result = api.shopping(*WEEK)
    assert result["estimated_total"] == 1.0
    assert result["unpriced_items"] == 1
    assert result["items"][0]["name"] == "Flour" and result["items"][0]["category"] == "Baking"


def test_editing_recipe_or_plan_changes_the_list(api, client):
    a = api.ingredient("A")
    b = api.ingredient("B")
    recipe = api.recipe("R", {a: 10})
    entry = api.plan("2026-10-05", "dinner", recipe)
    client.put(f"/api/recipes/{recipe}", json={"name": "R", "servings": 2, "items": [{"ingredient_id": b, "quantity": 5}]})
    client.put(f"/api/plan/{entry}", json={"date": "2026-10-05", "slot": "dinner", "recipe_id": recipe, "servings_multiplier": 3})
    assert api.to_buy(*WEEK) == {b: 15}


def test_invalid_range_rejected(client):
    assert client.get("/api/shopping-list", params={"start": "2026-10-11", "end": "2026-10-05"}).status_code == 422
    assert client.get("/api/shopping-list", params={"start": "nope", "end": "2026-10-05"}).status_code == 422
    assert client.get("/api/shopping-list").status_code == 422
