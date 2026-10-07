"""Core schema. Feature modules declare their own tables in their own migrations."""

from mealplanner.core.db import Migration

CORE_OWNER = "core"

CORE_MIGRATIONS = [
    Migration(
        1,
        """
        CREATE TABLE ingredients (
            id             INTEGER PRIMARY KEY,
            name           TEXT    NOT NULL COLLATE NOCASE UNIQUE,
            unit           TEXT    NOT NULL CHECK (unit IN ('g', 'ml', 'count')),
            price_per_unit REAL    CHECK (price_per_unit IS NULL OR price_per_unit >= 0),
            is_staple      INTEGER NOT NULL DEFAULT 0 CHECK (is_staple IN (0, 1)),
            category       TEXT
        );

        CREATE TABLE recipes (
            id       INTEGER PRIMARY KEY,
            name     TEXT    NOT NULL,
            servings INTEGER NOT NULL CHECK (servings > 0)
        );

        CREATE TABLE recipe_items (
            recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
            quantity      REAL    NOT NULL CHECK (quantity > 0),
            PRIMARY KEY (recipe_id, ingredient_id)
        );

        CREATE TABLE pantry (
            ingredient_id INTEGER PRIMARY KEY REFERENCES ingredients(id) ON DELETE CASCADE,
            quantity      REAL    NOT NULL CHECK (quantity >= 0)
        );

        CREATE TABLE plan (
            id                  INTEGER PRIMARY KEY,
            date                TEXT    NOT NULL,
            slot                TEXT    NOT NULL CHECK (slot IN ('breakfast', 'lunch', 'dinner')),
            recipe_id           INTEGER NOT NULL REFERENCES recipes(id) ON DELETE RESTRICT,
            servings_multiplier REAL    NOT NULL DEFAULT 1 CHECK (servings_multiplier > 0),
            eaten_at            TEXT,
            UNIQUE (date, slot)
        );
        CREATE INDEX plan_date ON plan(date);
        """,
    ),
]
