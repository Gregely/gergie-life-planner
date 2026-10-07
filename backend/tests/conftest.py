import pytest
from fastapi.testclient import TestClient

from mealplanner.main import create_app


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path))


class Api:
    """Small helpers so API tests read as scenarios rather than JSON plumbing."""

    def __init__(self, client: TestClient):
        self.c = client

    def _ok(self, resp, status=200):
        assert resp.status_code == status, resp.text
        return resp.json() if resp.content else None

    def ingredient(self, name, unit="g", price=None, staple=False, category=None):
        body = {"name": name, "unit": unit, "price_per_unit": price, "is_staple": staple, "category": category}
        return self._ok(self.c.post("/api/ingredients", json=body), 201)["id"]

    def recipe(self, name, items, servings=1):
        body = {"name": name, "servings": servings, "items": [{"ingredient_id": i, "quantity": q} for i, q in items.items()]}
        return self._ok(self.c.post("/api/recipes", json=body), 201)["id"]

    def stock(self, ingredient_id, quantity):
        return self._ok(self.c.put(f"/api/pantry/{ingredient_id}", json={"quantity": quantity}))

    def plan(self, date, slot, recipe_id, portions=1.0):
        body = {"date": date, "slot": slot, "recipe_id": recipe_id, "portions": portions}
        return self._ok(self.c.post("/api/plan", json=body), 201)["id"]

    def pantry(self):
        return {p["ingredient_id"]: p["quantity"] for p in self._ok(self.c.get("/api/pantry"))}

    def shopping(self, start, end):
        return self._ok(self.c.get("/api/shopping-list", params={"start": start, "end": end}))

    def to_buy(self, start, end):
        return {i["ingredient_id"]: i["to_buy"] for i in self.shopping(start, end)["items"]}

    def eat(self, plan_id):
        return self._ok(self.c.post(f"/api/plan/{plan_id}/eaten"))


@pytest.fixture
def api(client):
    return Api(client)
