"""CRUD behaviour and integrity rules for ingredients, recipes, pantry and plan."""


# --- ingredients ---

def test_ingredient_crud(client):
    resp = client.post("/api/ingredients", json={"name": " Flour ", "unit": "g", "price_per_unit": 0.002, "category": ""})
    assert resp.status_code == 201
    ing = resp.json()
    assert ing == {"id": ing["id"], "name": "Flour", "unit": "g", "price_per_unit": 0.002, "is_staple": False, "category": None}

    upd = client.put(f"/api/ingredients/{ing['id']}", json={"name": "Plain flour", "unit": "g", "is_staple": True})
    assert upd.json()["name"] == "Plain flour" and upd.json()["is_staple"] is True and upd.json()["price_per_unit"] is None
    assert [i["name"] for i in client.get("/api/ingredients").json()] == ["Plain flour"]

    assert client.delete(f"/api/ingredients/{ing['id']}").status_code == 204
    assert client.get(f"/api/ingredients/{ing['id']}").status_code == 404


def test_ingredient_validation(client):
    assert client.post("/api/ingredients", json={"name": "X", "unit": "kg"}).status_code == 422
    assert client.post("/api/ingredients", json={"name": "", "unit": "g"}).status_code == 422
    assert client.post("/api/ingredients", json={"name": "X", "unit": "g", "price_per_unit": -1}).status_code == 422
    assert client.post("/api/ingredients", json={"name": "X", "unit": "g", "colour": "red"}).status_code == 422


def test_ingredient_names_unique_case_insensitive(api, client):
    api.ingredient("Salt")
    assert client.post("/api/ingredients", json={"name": "salt", "unit": "g"}).status_code == 409
    other = api.ingredient("Pepper")
    assert client.put(f"/api/ingredients/{other}", json={"name": "SALT", "unit": "g"}).status_code == 409


def test_cannot_delete_ingredient_used_in_recipe(api, client):
    flour = api.ingredient("Flour")
    api.recipe("Bread", {flour: 500})
    resp = client.delete(f"/api/ingredients/{flour}")
    assert resp.status_code == 409 and "Bread" in resp.json()["detail"]


def test_deleting_ingredient_removes_its_pantry_row(api, client):
    flour = api.ingredient("Flour")
    api.stock(flour, 100)
    client.delete(f"/api/ingredients/{flour}")
    assert api.pantry() == {}


# --- recipes ---

def test_recipe_crud(api, client):
    flour, water = api.ingredient("Flour"), api.ingredient("Water", unit="ml")
    rid = api.recipe("Bread", {flour: 500, water: 300}, servings=8)
    recipe = client.get(f"/api/recipes/{rid}").json()
    assert recipe["servings"] == 8
    assert {(i["name"], i["unit"], i["quantity"]) for i in recipe["items"]} == {("Flour", "g", 500), ("Water", "ml", 300)}

    client.put(f"/api/recipes/{rid}", json={"name": "Flatbread", "servings": 4, "items": [{"ingredient_id": flour, "quantity": 250}]})
    recipe = client.get(f"/api/recipes/{rid}").json()
    assert recipe["name"] == "Flatbread" and [i["quantity"] for i in recipe["items"]] == [250]
    assert client.get("/api/recipes").json() == [{"id": rid, "name": "Flatbread", "servings": 4}]

    assert client.delete(f"/api/recipes/{rid}").status_code == 204
    assert client.get(f"/api/recipes/{rid}").status_code == 404


def test_recipe_validation(api, client):
    flour = api.ingredient("Flour")
    bad = [
        {"name": "R", "servings": 0, "items": []},
        {"name": "R", "servings": 2, "items": [{"ingredient_id": flour, "quantity": 0}]},
        {"name": "R", "servings": 2, "items": [{"ingredient_id": flour, "quantity": 1}, {"ingredient_id": flour, "quantity": 2}]},
        {"name": "R", "servings": 2, "items": [{"ingredient_id": 999, "quantity": 1}]},
    ]
    for body in bad:
        assert client.post("/api/recipes", json=body).status_code == 422, body
    assert client.get("/api/recipes").json() == []


def test_failed_recipe_update_leaves_recipe_intact(api, client):
    flour = api.ingredient("Flour")
    rid = api.recipe("Bread", {flour: 500})
    resp = client.put(f"/api/recipes/{rid}", json={"name": "X", "servings": 1, "items": [{"ingredient_id": 999, "quantity": 1}]})
    assert resp.status_code == 422
    assert client.get(f"/api/recipes/{rid}").json()["name"] == "Bread"


def test_cannot_delete_planned_recipe(api, client):
    rid = api.recipe("Soup", {})
    api.plan("2026-10-05", "lunch", rid)
    assert client.delete(f"/api/recipes/{rid}").status_code == 409


# --- pantry ---

def test_pantry_set_overwrite_delete(api, client):
    rice = api.ingredient("Rice")
    assert api.stock(rice, 1000)["quantity"] == 1000
    assert api.stock(rice, 250)["quantity"] == 250
    assert client.get(f"/api/pantry/{rice}").json() == {"ingredient_id": rice, "name": "Rice", "unit": "g", "quantity": 250}
    assert client.delete(f"/api/pantry/{rice}").status_code == 204
    assert client.get(f"/api/pantry/{rice}").status_code == 404


def test_pantry_validation(api, client):
    rice = api.ingredient("Rice")
    assert client.put(f"/api/pantry/{rice}", json={"quantity": -1}).status_code == 422
    assert client.put("/api/pantry/999", json={"quantity": 1}).status_code == 404


# --- plan ---

def test_plan_crud_and_ordering(api, client):
    rid = api.recipe("Soup", {})
    dinner = api.plan("2026-10-05", "dinner", rid)
    breakfast = api.plan("2026-10-05", "breakfast", rid, portions=0.5)
    api.plan("2026-10-04", "lunch", rid)
    week = client.get("/api/plan", params={"start": "2026-10-05", "end": "2026-10-11"}).json()
    assert [e["id"] for e in week] == [breakfast, dinner]
    assert week[0]["portions"] == 0.5 and week[0]["recipe_name"] == "Soup"

    moved = client.put(f"/api/plan/{dinner}", json={"date": "2026-10-06", "slot": "lunch", "recipe_id": rid})
    assert moved.json()["date"] == "2026-10-06" and moved.json()["portions"] == 1
    assert client.delete(f"/api/plan/{dinner}").status_code == 204
    assert client.get(f"/api/plan/{dinner}").status_code == 404


def test_plan_slot_is_unique(api, client):
    rid = api.recipe("Soup", {})
    api.plan("2026-10-05", "dinner", rid)
    other = api.plan("2026-10-05", "lunch", rid)
    body = {"date": "2026-10-05", "slot": "dinner", "recipe_id": rid}
    assert client.post("/api/plan", json=body).status_code == 409
    assert client.put(f"/api/plan/{other}", json=body).status_code == 409


def test_plan_validation(api, client):
    rid = api.recipe("Soup", {})
    bad = [
        {"date": "2026-10-05", "slot": "brunch", "recipe_id": rid},
        {"date": "2026-02-30", "slot": "lunch", "recipe_id": rid},
        {"date": "2026-10-05", "slot": "lunch", "recipe_id": rid, "portions": 0},
    ]
    for body in bad:
        assert client.post("/api/plan", json=body).status_code == 422, body
    assert client.post("/api/plan", json={"date": "2026-10-05", "slot": "lunch", "recipe_id": 999}).status_code == 404
    assert client.get("/api/plan", params={"start": "2026-10-11", "end": "2026-10-05"}).status_code == 422
