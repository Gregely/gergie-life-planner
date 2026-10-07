"""Tools the model can call. Every one wraps an existing backend function.

Read tools run immediately. Write tools never change data when the model
calls them: they are validated and stored as a *proposal* the person must
apply in the app. ``apply_proposal`` then performs it in one transaction
using the same functions the REST API uses, so there is no second copy of
any shopping or pantry maths here.

Write tools may refer to ingredients and recipes by id, or by name. A name may
point at something another pending proposal will create (e.g. a recipe that
uses a new ingredient); it is resolved when the proposal is applied.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from typing import Any, Callable

from pydantic import ValidationError

from mealplanner.core import repo
from mealplanner.core.db import transaction
from mealplanner.core.errors import Conflict, DomainError, Invalid, NotFound
from mealplanner.core.models import SLOTS, Ingredient, IngredientIn, PlanEntryIn, RecipeIn
from mealplanner.modules.ai import store
from mealplanner.modules.eaten import mark_eaten
from mealplanner.modules.shopping import BoughtIn, record_bought, shopping_list


class ToolError(Exception):
    """A problem the model should see and fix (sent back as an error tool_result)."""


# --- tool definitions sent to the model ------------------------------------------

_DATE = {"type": "string", "description": "Date as YYYY-MM-DD"}
_INGREDIENT_REF = {
    "ingredient_id": {"type": "integer", "description": "Existing ingredient id"},
    "ingredient_name": {
        "type": "string",
        "description": "Ingredient name, if it has no id yet because a pending create_ingredient proposal adds it",
    },
}


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


READ_TOOLS = [
    _tool("list_ingredients", "List every ingredient: id, name, base unit (g, ml or count), price per base unit, staple flag, category.", {}),
    _tool("list_recipes", "List every recipe with its servings (portions one batch makes) and ingredient quantities for one batch.", {}),
    _tool("get_pantry", "List what is in the pantry now, in each ingredient's base unit.", {}),
    _tool("get_plan", "List planned meals (with plan entry ids and eaten status) between two dates, inclusive.",
          {"start": _DATE, "end": _DATE}, ["start", "end"]),
    _tool("get_shopping_list", "The shopping list for planned, uneaten meals between two dates: what to buy after subtracting the pantry. Staples are excluded.",
          {"start": _DATE, "end": _DATE}, ["start", "end"]),
]

WRITE_TOOLS = [
    _tool("create_ingredient", "Propose adding a new ingredient. Check list_ingredients first; never propose a duplicate.", {
        "name": {"type": "string"},
        "unit": {"type": "string", "enum": ["g", "ml", "count"], "description": "Base unit all quantities use"},
        "price_per_unit": {"type": "number", "description": "Price per base unit. Only if the person told you the price; never estimate"},
        "is_staple": {"type": "boolean", "description": "Staples (salt, oil, spices) never appear on shopping lists"},
        "category": {"type": "string", "description": "Optional shop grouping, e.g. Produce, Dairy"},
    }, ["name", "unit"]),
    _tool("create_recipe", "Propose a new recipe. Quantities are for one batch, in each ingredient's base unit.", {
        "name": {"type": "string"},
        "servings": {"type": "integer", "description": "Portions one batch makes"},
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {**_INGREDIENT_REF, "quantity": {"type": "number", "description": "Base units for one batch"}},
            "required": ["quantity"],
            "additionalProperties": False,
        }},
    }, ["name", "servings", "items"]),
    _tool("plan_meal", "Propose planning a recipe for a date and meal slot. Replaces whatever is planned in that slot.", {
        "date": _DATE,
        "slot": {"type": "string", "enum": list(SLOTS)},
        "recipe_id": {"type": "integer"},
        "recipe_name": {"type": "string", "description": "Only for a recipe a pending create_recipe proposal adds"},
        "portions": {"type": "number", "description": "Portions eaten at this meal (default 1)"},
    }, ["date", "slot"]),
    _tool("remove_plan_entry", "Propose removing a planned meal.", {"plan_id": {"type": "integer"}}, ["plan_id"]),
    _tool("update_pantry", "Propose setting the pantry quantity of one ingredient to an absolute amount in its base unit.", {
        **_INGREDIENT_REF,
        "quantity": {"type": "number", "description": "New total amount in the pantry, in base units"},
    }, ["quantity"]),
    _tool("record_bought", "Propose adding bought shopping-list items to the pantry. Omit quantity to use the list's to-buy amount for the date range.", {
        "start": _DATE,
        "end": _DATE,
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {**_INGREDIENT_REF, "quantity": {"type": "number", "description": "Base units bought; omit for the to-buy amount"}},
            "additionalProperties": False,
        }},
    }, ["start", "end", "items"]),
    _tool("mark_eaten", "Propose marking a planned meal as eaten, which takes its ingredients out of the pantry.",
          {"plan_id": {"type": "integer"}}, ["plan_id"]),
]

TOOLS = READ_TOOLS + WRITE_TOOLS
WRITE_TOOL_NAMES = {t["name"] for t in WRITE_TOOLS}


# --- helpers -------------------------------------------------------------------


def _date(value: Any, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ToolError(f"{field} must be a date as YYYY-MM-DD, got {value!r}") from None


def _range(data: dict) -> tuple[dt.date, dt.date]:
    start, end = _date(data.get("start"), "start"), _date(data.get("end"), "end")
    if end < start:
        raise ToolError("end must be on or after start")
    return start, end


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("oes"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalise_name(name: str) -> str:
    """Loose form of a name for duplicate checks: case, punctuation and plurals ignored."""
    words = re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
    return " ".join(_singular(w) for w in words)


def _find_ingredient(conn: sqlite3.Connection, name: str) -> Ingredient | None:
    wanted = normalise_name(name)
    for ingredient in repo.list_ingredients(conn):
        if normalise_name(ingredient.name) == wanted:
            return ingredient
    return None


def _pending_inputs(conn: sqlite3.Connection, session_id: str, tool: str) -> list[tuple[int, dict]]:
    return [(p["id"], json.loads(p["input"])) for p in store.pending(conn, session_id) if p["tool"] == tool]


def _fmt(quantity: float, unit: str) -> str:
    text = f"{quantity:g}"
    return text if unit == "count" else f"{text} {unit}"


def _day(date: dt.date) -> str:
    return f"{date:%a} {date.day} {date:%b}"


class _Ref:
    """An ingredient or recipe reference: an existing id, or a name pending creation."""

    def __init__(self, id: int | None, name: str, unit: str | None = None):
        self.id, self.name, self.unit = id, name, unit


def _ingredient_ref(conn, session_id, data: dict, *, allow_pending: bool) -> _Ref:
    ingredient_id, name = data.get("ingredient_id"), data.get("ingredient_name")
    if ingredient_id is not None:
        try:
            i = repo.get_ingredient(conn, int(ingredient_id))
        except NotFound:
            raise ToolError(f"no ingredient with id {ingredient_id}; call list_ingredients") from None
        return _Ref(i.id, i.name, i.unit)
    if not name:
        raise ToolError("give ingredient_id (or ingredient_name for an ingredient being proposed)")
    existing = _find_ingredient(conn, name)
    if existing:
        return _Ref(existing.id, existing.name, existing.unit)
    if allow_pending:
        for _, pending in _pending_inputs(conn, session_id, "create_ingredient"):
            if normalise_name(pending.get("name", "")) == normalise_name(name):
                return _Ref(None, pending["name"], pending.get("unit"))
    raise ToolError(f"no ingredient called {name!r}; check list_ingredients or propose create_ingredient first")


def _ingredient_id_at_apply(conn, data: dict) -> int:
    if data.get("ingredient_id") is not None:
        return repo.get_ingredient(conn, int(data["ingredient_id"])).id
    existing = _find_ingredient(conn, data.get("ingredient_name") or "")
    if existing is None:
        raise Conflict(f"ingredient {data.get('ingredient_name')!r} doesn't exist yet: apply the proposal that adds it first")
    return existing.id


def _find_recipe(conn, name: str):
    wanted = normalise_name(name)
    return next((r for r in repo.list_recipes(conn) if normalise_name(r.name) == wanted), None)


def _recipe_ref(conn, session_id, data: dict) -> _Ref:
    if data.get("recipe_id") is not None:
        try:
            r = repo.get_recipe(conn, int(data["recipe_id"]))
        except NotFound:
            raise ToolError(f"no recipe with id {data['recipe_id']}; call list_recipes") from None
        return _Ref(r.id, r.name)
    name = data.get("recipe_name")
    if not name:
        raise ToolError("give recipe_id (or recipe_name for a recipe being proposed)")
    existing = _find_recipe(conn, name)
    if existing:
        return _Ref(existing.id, existing.name)
    for _, pending in _pending_inputs(conn, session_id, "create_recipe"):
        if normalise_name(pending.get("name", "")) == normalise_name(name):
            return _Ref(None, pending["name"])
    raise ToolError(f"no recipe called {name!r}; check list_recipes or propose create_recipe first")


def _recipe_id_at_apply(conn, data: dict) -> int:
    if data.get("recipe_id") is not None:
        return repo.get_recipe(conn, int(data["recipe_id"])).id
    existing = _find_recipe(conn, data.get("recipe_name") or "")
    if existing is None:
        raise Conflict(f"recipe {data.get('recipe_name')!r} doesn't exist yet: apply the proposal that adds it first")
    return existing.id


def _plan_entry(conn, plan_id: Any):
    try:
        return repo.get_plan_entry(conn, int(plan_id))
    except (NotFound, TypeError, ValueError):
        raise ToolError(f"no plan entry with id {plan_id}; call get_plan") from None


def _slot_entry(conn, date: dt.date, slot: str):
    return next((e for e in repo.list_plan(conn, date, date) if e.slot == slot), None)


# --- read tools ------------------------------------------------------------------


def _dump(models) -> list[dict]:
    return [m.model_dump(mode="json") for m in models]


READERS: dict[str, Callable[[sqlite3.Connection, dict], Any]] = {
    "list_ingredients": lambda conn, _: _dump(repo.list_ingredients(conn)),
    "list_recipes": lambda conn, _: _dump(repo.get_recipe(conn, r.id) for r in repo.list_recipes(conn)),
    "get_pantry": lambda conn, _: _dump(repo.list_pantry(conn)),
    "get_plan": lambda conn, data: _dump(repo.list_plan(conn, *_range(data))),
    "get_shopping_list": lambda conn, data: shopping_list(conn, *_range(data)).model_dump(mode="json"),
}


# --- write tools: validate and describe (proposal time) ---------------------------
# Each returns (summary, details) for the proposal card, or raises ToolError.


def _propose_create_ingredient(conn, session_id, data):
    try:
        ingredient = IngredientIn(**data)
    except ValidationError as exc:
        raise ToolError(_validation_message(exc)) from None
    existing = _find_ingredient(conn, ingredient.name)
    if existing:
        raise ToolError(f"ingredient already exists: {existing.name!r} (id {existing.id}, unit {existing.unit}); use it")
    for pid, pending in _pending_inputs(conn, session_id, "create_ingredient"):
        if normalise_name(pending.get("name", "")) == normalise_name(ingredient.name):
            raise ToolError(f"already proposed as #{pid}")
    unit_word = "item" if ingredient.unit == "count" else ingredient.unit
    details = [f"Unit: {ingredient.unit}"]
    details.append("No price" if ingredient.price_per_unit is None else f"Price: {ingredient.price_per_unit:g} per {unit_word}")
    if ingredient.category:
        details.append(f"Category: {ingredient.category}")
    if ingredient.is_staple:
        details.append("Staple (never on the shopping list)")
    return f'Add ingredient "{ingredient.name}"', details


def _propose_create_recipe(conn, session_id, data):
    name = str(data.get("name") or "").strip()
    if not name:
        raise ToolError("name is required")
    if not isinstance(data.get("servings"), int) or data["servings"] < 1:
        raise ToolError("servings must be a whole number of portions, at least 1")
    existing = _find_recipe(conn, name)
    if existing:
        raise ToolError(f"recipe already exists: {existing.name!r} (id {existing.id})")
    items = data.get("items") or []
    details, seen = [], set()
    for item in items:
        ref = _ingredient_ref(conn, session_id, item, allow_pending=True)
        key = normalise_name(ref.name)
        if key in seen:
            raise ToolError(f"{ref.name} is listed more than once")
        seen.add(key)
        quantity = item.get("quantity")
        if not isinstance(quantity, (int, float)) or quantity <= 0:
            raise ToolError(f"{ref.name}: quantity must be a positive number in {ref.unit or 'its base unit'}")
        details.append(f"{ref.name}: {_fmt(quantity, ref.unit or '')}".rstrip() + ("" if ref.id else " (new ingredient)"))
    return f'New recipe "{name}" (makes {data["servings"]})', details or ["No ingredients"]


def _propose_plan_meal(conn, session_id, data):
    date = _date(data.get("date"), "date")
    slot = data.get("slot")
    if slot not in SLOTS:
        raise ToolError(f"slot must be one of {', '.join(SLOTS)}")
    portions = data.get("portions", 1)
    if not isinstance(portions, (int, float)) or portions <= 0:
        raise ToolError("portions must be a positive number")
    recipe = _recipe_ref(conn, session_id, data)
    current = _slot_entry(conn, date, slot)
    if current and current.eaten_at:
        raise ToolError(f"{slot} on {date} was already eaten ({current.recipe_name}); it can't be replaced")
    details = [f"{portions:g} portion{'s' if portions != 1 else ''}"]
    if current:
        details.append(f'Replaces "{current.recipe_name}"')
    return f'Plan "{recipe.name}" for {slot}, {_day(date)}', details


def _propose_remove_plan_entry(conn, session_id, data):
    entry = _plan_entry(conn, data.get("plan_id"))
    return f'Remove "{entry.recipe_name}" from {entry.slot}, {_day(entry.date)}', (["Already eaten"] if entry.eaten_at else [])


def _propose_update_pantry(conn, session_id, data):
    ref = _ingredient_ref(conn, session_id, data, allow_pending=True)
    quantity = data.get("quantity")
    if not isinstance(quantity, (int, float)) or quantity < 0:
        raise ToolError("quantity must be zero or more, in the ingredient's base unit")
    current = repo.pantry_quantities(conn).get(ref.id) if ref.id else None
    unit = ref.unit or ""
    detail = "Not in the pantry now" if current is None else f"Now: {_fmt(current, unit)}"
    return f"Set pantry {ref.name} to {_fmt(quantity, unit)}", [detail]


def _propose_record_bought(conn, session_id, data):
    start, end = _range(data)
    items = data.get("items") or []
    if not items:
        raise ToolError("items must list at least one ingredient")
    to_buy = {i.ingredient_id: i.to_buy for i in shopping_list(conn, start, end).items}
    details, seen = [], set()
    for item in items:
        ref = _ingredient_ref(conn, session_id, item, allow_pending=False)
        if ref.id in seen:
            raise ToolError(f"{ref.name} is listed more than once")
        seen.add(ref.id)
        quantity = item.get("quantity")
        if quantity is None:
            if ref.id not in to_buy:
                raise ToolError(f"{ref.name} is not on the shopping list for these dates; give the quantity bought")
            details.append(f"{ref.name}: {_fmt(to_buy[ref.id], ref.unit)} (amount on the list)")
        elif not isinstance(quantity, (int, float)) or quantity <= 0:
            raise ToolError(f"{ref.name}: quantity must be a positive number in {ref.unit}")
        else:
            details.append(f"{ref.name}: {_fmt(quantity, ref.unit)}")
    return f"Add {len(items)} bought item{'s' if len(items) != 1 else ''} to the pantry", details


def _propose_mark_eaten(conn, session_id, data):
    entry = _plan_entry(conn, data.get("plan_id"))
    if entry.eaten_at:
        raise ToolError("that meal is already marked as eaten")
    for pid, pending in _pending_inputs(conn, session_id, "mark_eaten"):
        if pending.get("plan_id") == entry.id:
            raise ToolError(f"already proposed as #{pid}")
    return (
        f'Mark "{entry.recipe_name}" ({entry.slot}, {_day(entry.date)}) as eaten',
        [f"{entry.portions:g} portion{'s' if entry.portions != 1 else ''}", "Takes its ingredients out of the pantry"],
    )


PROPOSERS = {
    "create_ingredient": _propose_create_ingredient,
    "create_recipe": _propose_create_recipe,
    "plan_meal": _propose_plan_meal,
    "remove_plan_entry": _propose_remove_plan_entry,
    "update_pantry": _propose_update_pantry,
    "record_bought": _propose_record_bought,
    "mark_eaten": _propose_mark_eaten,
}


# --- write tools: perform (apply time) ------------------------------------------


def _apply_create_ingredient(conn, data):
    return repo.create_ingredient(conn, IngredientIn(**data)).model_dump(mode="json")


def _apply_create_recipe(conn, data):
    items = [{"ingredient_id": _ingredient_id_at_apply(conn, i), "quantity": i["quantity"]} for i in data.get("items") or []]
    return repo.create_recipe(conn, RecipeIn(name=data["name"], servings=data["servings"], items=items)).model_dump(mode="json")


def _apply_plan_meal(conn, data):
    entry = PlanEntryIn(date=data["date"], slot=data["slot"], recipe_id=_recipe_id_at_apply(conn, data),
                        portions=data.get("portions", 1))
    current = _slot_entry(conn, entry.date, entry.slot)
    if current and current.eaten_at:
        raise Conflict(f"{entry.slot} on {entry.date} was already eaten; it can't be replaced")
    if current:
        return repo.update_plan_entry(conn, current.id, entry).model_dump(mode="json")
    return repo.create_plan_entry(conn, entry).model_dump(mode="json")


def _apply_remove_plan_entry(conn, data):
    repo.delete_plan_entry(conn, int(data["plan_id"]))
    return {"removed_plan_id": int(data["plan_id"])}


def _apply_update_pantry(conn, data):
    return repo.set_pantry_quantity(conn, _ingredient_id_at_apply(conn, data), data["quantity"]).model_dump(mode="json")


def _apply_record_bought(conn, data):
    items = [{"ingredient_id": _ingredient_id_at_apply(conn, i), "quantity": i.get("quantity")} for i in data["items"]]
    return record_bought(conn, BoughtIn(start=data["start"], end=data["end"], items=items)).model_dump(mode="json")


def _apply_mark_eaten(conn, data):
    return mark_eaten(conn, int(data["plan_id"])).model_dump(mode="json")


APPLIERS = {
    "create_ingredient": _apply_create_ingredient,
    "create_recipe": _apply_create_recipe,
    "plan_meal": _apply_plan_meal,
    "remove_plan_entry": _apply_remove_plan_entry,
    "update_pantry": _apply_update_pantry,
    "record_bought": _apply_record_bought,
    "mark_eaten": _apply_mark_eaten,
}


# --- entry points ----------------------------------------------------------------


def run_tool(conn: sqlite3.Connection, session_id: str, name: str, data: dict) -> tuple[Any, int | None]:
    """Run a read tool, or store a proposal for a write tool.

    Returns ``(result for the model, proposal id or None)``. Raises ToolError
    for anything the model should correct.
    """
    if not isinstance(data, dict):
        raise ToolError("tool input must be an object")
    try:
        if name in READERS:
            return READERS[name](conn, data), None
        if name in PROPOSERS:
            summary, details = PROPOSERS[name](conn, session_id, data)
            proposal_id = store.add_proposal(conn, session_id, name, data, summary, details)
            return {
                "proposal_id": proposal_id,
                "status": "pending: NOT applied. The person must tap Apply in the app",
                "summary": summary,
                "details": details,
            }, proposal_id
    except DomainError as exc:
        raise ToolError(str(exc)) from None
    raise ToolError(f"unknown tool {name!r}")


def apply_proposal(conn: sqlite3.Connection, proposal_id: int) -> tuple[dict, Any]:
    """Perform a pending proposal atomically. Nothing is changed if any step fails."""
    with transaction(conn):
        row = store.get_proposal(conn, proposal_id)
        if row["status"] != "pending":
            raise Conflict(f"proposal #{proposal_id} is already {row['status']}")
        try:
            result = APPLIERS[row["tool"]](conn, json.loads(row["input"]))
        except ValidationError as exc:
            raise Invalid(_validation_message(exc)) from None
        store.decide(conn, proposal_id, "applied", result)
    return store.proposal_view(store.get_proposal(conn, proposal_id)), result


def reject_proposal(conn: sqlite3.Connection, proposal_id: int) -> dict:
    with transaction(conn):
        row = store.get_proposal(conn, proposal_id)
        if row["status"] != "pending":
            raise Conflict(f"proposal #{proposal_id} is already {row['status']}")
        store.decide(conn, proposal_id, "rejected")
    return store.proposal_view(store.get_proposal(conn, proposal_id))
