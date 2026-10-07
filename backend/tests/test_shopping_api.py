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


def test_four_single_portions_of_four_serving_recipe_need_exactly_one_batch(api):
    mince = api.ingredient("Mince")
    onion = api.ingredient("Onion", unit="count")
    chilli = api.recipe("Chilli", {mince: 500, onion: 2}, servings=4)
    for day in ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"):
        api.plan(day, "dinner", chilli, portions=1)
    assert api.to_buy(*WEEK) == {mince: 500, onion: 2}


def test_two_portions_of_four_serving_recipe_need_half_a_batch(api):
    mince = api.ingredient("Mince")
    onion = api.ingredient("Onion", unit="count")
    api.plan("2026-10-05", "dinner", api.recipe("Chilli", {mince: 500, onion: 2}, servings=4), portions=2)
    assert api.to_buy(*WEEK) == {mince: 250, onion: 1}


def test_one_portion_is_recipe_quantity_divided_by_servings(api):
    mince = api.ingredient("Mince")
    api.plan("2026-10-05", "dinner", api.recipe("Chilli", {mince: 500}, servings=4), portions=1)
    assert api.to_buy(*WEEK) == {mince: 125}


def test_thirds_add_up_to_exactly_one_batch(api):
    # 100 / 3 is not exact in floating point; three portions must still be 100, not 100.00000000000001.
    stock = api.ingredient("Stock", unit="ml")
    soup = api.recipe("Soup", {stock: 100}, servings=3)
    for slot in ("breakfast", "lunch", "dinner"):
        api.plan("2026-10-05", slot, soup, portions=1)
    assert api.to_buy(*WEEK) == {stock: 100}
    api.stock(stock, 100)
    assert api.to_buy(*WEEK) == {}


def test_fractional_and_multiple_portions(api):
    pasta = api.ingredient("Pasta")
    recipe = api.recipe("Pasta", {pasta: 200}, servings=2)
    api.plan("2026-10-05", "dinner", recipe, portions=2.5)  # 250 g
    api.plan("2026-10-06", "lunch", recipe, portions=0.5)  # 50 g
    api.stock(pasta, 50)
    assert api.to_buy(*WEEK) == {pasta: 250}


def test_changing_recipe_servings_changes_needs_for_same_portions(api, client):
    rice = api.ingredient("Rice")
    recipe = api.recipe("Rice", {rice: 400}, servings=4)
    api.plan("2026-10-05", "dinner", recipe, portions=2)
    assert api.to_buy(*WEEK) == {rice: 200}
    client.put(f"/api/recipes/{recipe}", json={"name": "Rice", "servings": 8, "items": [{"ingredient_id": rice, "quantity": 400}]})
    assert api.to_buy(*WEEK) == {rice: 100}


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
    client.put(f"/api/plan/{entry}", json={"date": "2026-10-05", "slot": "dinner", "recipe_id": recipe, "portions": 3})
    assert api.to_buy(*WEEK) == {b: 7.5}  # 5 per 2-serving batch x 3 portions


def test_invalid_range_rejected(client):
    assert client.get("/api/shopping-list", params={"start": "2026-10-11", "end": "2026-10-05"}).status_code == 422
    assert client.get("/api/shopping-list", params={"start": "nope", "end": "2026-10-05"}).status_code == 422
    assert client.get("/api/shopping-list").status_code == 422
