# Meal Planner

A small, modular meal planner meant to run on a Raspberry Pi. It has a FastAPI + SQLite backend and (from stage 2) a mobile-first PWA.

**Status: stages 1 (data layer and logic) and 2 (frontend) are done.**

## Run it

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

pytest                                    # run the tests
uvicorn --factory mealplanner.main:create_app --host 0.0.0.0 --port 8000
```

Open `http://<host>:8000/` for the app and `http://<host>:8000/docs` for interactive API docs. The database is a single file at `backend/data/mealplanner.db`. You can override that path with `MEALPLANNER_DB=/path/to/file.db`.

## Frontend

`frontend/` is a plain HTML/CSS/JS progressive web app with no build step. The backend serves it from the same origin as the API, so there's nothing extra to run. Set `MEALPLANNER_FRONTEND=/path` to serve it from somewhere else.

Screens (bottom nav):

- **Plan**: a 7-day × breakfast/lunch/dinner grid. Tap a cell to pick a recipe and the portions eaten, or to remove it. The ‹ › buttons move between weeks.
- **Shop**: the shopping list for a date range (defaults to this week), grouped by ingredient category and checkable. Check marks are kept in the phone's browser storage, per date range.
- **Recipes**: name, servings (portions one batch makes) and the ingredients for one batch.
- **Ingredients**: name, base unit, optional price per unit, staple flag, optional category.
- **Pantry**: every ingredient with an editable quantity. Each change saves as you go, and a blank field removes the item from the pantry.

The frontend does no quantity maths. It only displays what the API returns.

**Installing on a phone:** browsers only offer "Install app" or "Add to Home Screen" as a full PWA over HTTPS (or on `localhost`). Over plain `http://<pi-ip>:8000` the app still works fully in the browser. Stage 3 covers getting HTTPS through Tailscale.

## Layout

```
frontend/              index.html, app.js, style.css, sw.js, manifest.webmanifest, icons/
backend/mealplanner/
  main.py              app factory: runs migrations, mounts every discovered module
  core/
    db.py              connection, transactions, per-owner migration runner
    schema.py          core tables (ingredients, recipes, recipe_items, pantry, plan)
    models.py          shared pydantic models
    repo.py            data access for core entities (used by routers, later by AI tools)
    quantities.py      shared quantity maths (aggregate plan requirements, float cleanup)
    module.py          the Module contract + auto-discovery
    errors.py          NotFound / Conflict / Invalid -> 404 / 409 / 422
  modules/
    ingredients/ recipes/ pantry/ plan/   CRUD routers
    shopping/          shopping list (logic.py is the pure maths)
    eaten/             mark-as-eaten + its own consumption_log table (logic.py is the pure maths)
```

### Adding a module

Create `mealplanner/modules/<name>/__init__.py` that defines:

```python
module = Module(name="<name>", router=router, migrations=[Migration(1, "CREATE TABLE ...")])
```

The app discovers it at startup, applies its migrations and mounts its router under `/api`. You don't edit any existing file. Migrations are tracked per module in `schema_migrations`.

## API (all under `/api`)

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/ingredients` | `{name, unit: g\|ml\|count, price_per_unit?, is_staple, category?}` |
| GET/PUT/DELETE | `/ingredients/{id}` | Deleting returns 409 if a recipe uses the ingredient |
| GET/POST | `/recipes` | `{name, servings, items: [{ingredient_id, quantity}]}` |
| GET/PUT/DELETE | `/recipes/{id}` | PUT replaces the ingredient list. Deleting returns 409 if the recipe is planned |
| GET | `/pantry` | |
| GET/PUT/DELETE | `/pantry/{ingredient_id}` | PUT `{quantity}` sets an absolute amount |
| GET | `/plan?start=&end=` | Inclusive date range |
| POST | `/plan` | `{date, slot: breakfast\|lunch\|dinner, recipe_id, portions=1}`. One entry per date+slot |
| GET/PUT/DELETE | `/plan/{id}` | |
| GET | `/shopping-list?start=&end=` | Shortfalls only, with cost estimate |
| POST | `/plan/{id}/eaten` | Deducts from the pantry and reports shortfalls. Returns 409 if the meal is already eaten |
| GET | `/plan/{id}/eaten` | What that meal took from the pantry |

All quantities are in the ingredient's base unit. There's no unit conversion.

## Rules

- **Portions.** A recipe's `servings` is how many portions one batch makes. A plan entry's `portions` is how many portions are eaten at that meal. Ingredient needs = recipe quantity × portions ÷ recipe servings. So a 4-serving chilli planned on 4 nights at 1 portion each needs exactly one batch, and 2 portions needs half a batch.
- **Shopping list** for a date range = sum of (recipe quantity × portions ÷ servings) over planned meals that haven't been eaten, minus what's in the pantry, keeping only positive shortfalls. Staples are never listed. Eaten meals are skipped because their ingredients already came out of the pantry.
- **Mark as eaten** takes `min(required, in pantry)` for each ingredient, so the pantry never goes below zero. Any remainder is reported as a shortfall. Ingredients with no pantry row count as 0 and no row is created for them. Staples are deducted like anything else, and their shortfalls are flagged with `is_staple` so the UI can play them down. The whole operation runs in one transaction.
