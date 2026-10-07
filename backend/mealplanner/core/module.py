"""The contract every feature module fulfils.

A module is a package under ``mealplanner/modules/`` whose ``__init__.py``
defines ``module = Module(...)``. It is discovered automatically at start-up:
adding a feature means adding a package, never editing an existing one.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType

from fastapi import APIRouter

from mealplanner.core.db import Migration


@dataclass(frozen=True)
class Module:
    name: str
    router: APIRouter | None = None
    migrations: list[Migration] = field(default_factory=list)
    description: str = ""


def discover_modules(package: ModuleType) -> list[Module]:
    """Import every sub-package of ``package`` that exposes a ``module`` attribute."""
    found: list[Module] = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        if not info.ispkg or info.name.startswith("_"):
            continue
        imported = importlib.import_module(f"{package.__name__}.{info.name}")
        mod = getattr(imported, "module", None)
        if isinstance(mod, Module):
            found.append(mod)
    names = [m.name for m in found]
    if len(set(names)) != len(names) or "core" in names:
        raise RuntimeError(f"module names must be unique and not 'core': {names}")
    return found
