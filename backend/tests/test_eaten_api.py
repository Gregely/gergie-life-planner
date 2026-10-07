"""Mark-as-eaten end to end: pantry deduction, clamping at zero, shortfall reporting."""


def test_deducts_ingredients_from_pantry(api):
    pasta = api.ingredient("Pasta")
    sauce = api.ingredient("Sauce", unit="ml")
    entry = api.plan("2026-10-05", "dinner", api.recipe("Pasta", {pasta: 100, sauce: 200}))
    api.stock(pasta, 500)
    api.stock(sauce, 500)
    result = api.eat(entry)
    assert api.pantry() == {pasta: 400, sauce: 300}
    assert result["shortfalls"] == []
    assert result["recipe_name"] == "Pasta" and result["eaten_at"]


def test_servings_multiplier_scales_deduction(api):
    rice = api.ingredient("Rice")
    entry = api.plan("2026-10-05", "dinner", api.recipe("Rice", {rice: 80}), multiplier=2.5)
    api.stock(rice, 1000)
    api.eat(entry)
    assert api.pantry() == {rice: 800}


def test_never_goes_below_zero_and_reports_shortfall(api):
    milk = api.ingredient("Milk", unit="ml")
    entry = api.plan("2026-10-05", "breakfast", api.recipe("Cereal", {milk: 300}))
    api.stock(milk, 100)
    result = api.eat(entry)
    assert api.pantry() == {milk: 0}
    [short] = result["shortfalls"]
    assert short["ingredient_id"] == milk
    assert (short["required"], short["available"], short["deducted"], short["remaining"], short["shortfall"]) == (300, 100, 100, 0, 200)


def test_missing_pantry_item_reported_and_not_created(api):
    eggs = api.ingredient("Eggs", unit="count")
    butter = api.ingredient("Butter")
    entry = api.plan("2026-10-05", "breakfast", api.recipe("Omelette", {eggs: 3, butter: 10}))
    api.stock(butter, 250)
    result = api.eat(entry)
    assert api.pantry() == {butter: 240}  # no eggs row appears
    assert [(s["name"], s["shortfall"]) for s in result["shortfalls"]] == [("Eggs", 3)]


def test_staples_are_deducted_and_flagged(api):
    salt = api.ingredient("Salt", staple=True)
    oil = api.ingredient("Oil", unit="ml", staple=True)
    entry = api.plan("2026-10-05", "dinner", api.recipe("Chips", {salt: 5, oil: 50}))
    api.stock(oil, 30)
    result = api.eat(entry)
    assert api.pantry() == {oil: 0}
    assert {(s["name"], s["shortfall"], s["is_staple"]) for s in result["shortfalls"]} == {("Salt", 5, True), ("Oil", 20, True)}


def test_two_meals_sharing_an_ingredient_deplete_sequentially(api):
    cheese = api.ingredient("Cheese")
    lunch = api.plan("2026-10-05", "lunch", api.recipe("Toastie", {cheese: 60}))
    dinner = api.plan("2026-10-05", "dinner", api.recipe("Pizza", {cheese: 100}))
    api.stock(cheese, 120)
    assert api.eat(lunch)["shortfalls"] == []
    assert api.pantry() == {cheese: 60}
    assert api.eat(dinner)["shortfalls"][0]["shortfall"] == 40
    assert api.pantry() == {cheese: 0}


def test_only_the_eaten_meal_is_deducted(api):
    jam = api.ingredient("Jam")
    toast = api.recipe("Toast", {jam: 20})
    today = api.plan("2026-10-05", "breakfast", toast)
    api.plan("2026-10-06", "breakfast", toast)
    api.stock(jam, 100)
    api.eat(today)
    assert api.pantry() == {jam: 80}


def test_cannot_eat_twice(api, client):
    tea = api.ingredient("Tea", unit="count")
    entry = api.plan("2026-10-05", "breakfast", api.recipe("Tea", {tea: 1}))
    api.stock(tea, 10)
    api.eat(entry)
    resp = client.post(f"/api/plan/{entry}/eaten")
    assert resp.status_code == 409
    assert api.pantry() == {tea: 9}


def test_marks_plan_entry_and_logs_consumption(api, client):
    oats = api.ingredient("Oats")
    entry = api.plan("2026-10-05", "breakfast", api.recipe("Porridge", {oats: 50}))
    api.stock(oats, 30)
    api.eat(entry)
    assert client.get(f"/api/plan/{entry}").json()["eaten_at"] is not None
    [log] = client.get(f"/api/plan/{entry}/eaten").json()
    assert (log["name"], log["required"], log["deducted"], log["shortfall"]) == ("Oats", 50, 30, 20)


def test_recipe_with_no_ingredients(api):
    entry = api.plan("2026-10-05", "dinner", api.recipe("Takeaway", {}))
    result = api.eat(entry)
    assert result["deductions"] == [] and result["shortfalls"] == []


def test_unknown_plan_entry(client):
    assert client.post("/api/plan/999/eaten").status_code == 404
    assert client.get("/api/plan/999/eaten").status_code == 404


def test_failure_after_pantry_write_rolls_everything_back(api, client, db_path):
    import sqlite3

    import pytest

    flour = api.ingredient("Flour")
    entry = api.plan("2026-10-05", "dinner", api.recipe("Bread", {flour: 100}))
    api.stock(flour, 500)
    # Break the log insert, which runs after the pantry UPDATE.
    sqlite3.connect(db_path).execute("DROP TABLE consumption_log")
    with pytest.raises(sqlite3.OperationalError):
        client.post(f"/api/plan/{entry}/eaten")
    assert api.pantry() == {flour: 500}
    assert client.get(f"/api/plan/{entry}").json()["eaten_at"] is None
