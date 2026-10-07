"""AI settings, all from environment variables so nothing needs a code edit.

ANTHROPIC_API_KEY            required to enable chat; read on the server only
MEALPLANNER_AI_MODEL         model id (default: claude-haiku-5-5, small and cheap)
MEALPLANNER_AI_EFFORT        optional: low | medium | high (unset = model default)
MEALPLANNER_AI_MAX_ITERATIONS  model calls allowed per chat message (default 8)
MEALPLANNER_AI_MAX_MESSAGE_CHARS  longest message accepted (default 2000)
MEALPLANNER_AI_MAX_TURNS     messages per chat session before a new chat is needed (default 40)
MEALPLANNER_AI_MAX_TOKENS    max_tokens per model call (default 4096)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL = "claude-haiku-5-5"


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(int(os.environ.get(name, default)), minimum)
    except ValueError:
        return default


@dataclass(frozen=True)
class AISettings:
    model: str
    effort: str | None
    max_iterations: int
    max_message_chars: int
    max_turns: int
    max_tokens: int


def load_settings() -> AISettings:
    effort = (os.environ.get("MEALPLANNER_AI_EFFORT") or "").strip().lower() or None
    return AISettings(
        model=(os.environ.get("MEALPLANNER_AI_MODEL") or "").strip() or DEFAULT_MODEL,
        effort=effort if effort in {"low", "medium", "high"} else None,
        max_iterations=_int("MEALPLANNER_AI_MAX_ITERATIONS", 8, minimum=2),
        max_message_chars=_int("MEALPLANNER_AI_MAX_MESSAGE_CHARS", 2000),
        max_turns=_int("MEALPLANNER_AI_MAX_TURNS", 40),
        max_tokens=_int("MEALPLANNER_AI_MAX_TOKENS", 4096, minimum=1024),
    )


def api_key() -> str | None:
    """The key comes from the server's environment only; it is never sent to the browser."""
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None
