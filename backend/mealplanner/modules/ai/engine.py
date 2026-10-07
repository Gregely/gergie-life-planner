"""The server-side tool-use loop for one chat message."""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any

from mealplanner.core.db import transaction
from mealplanner.core.errors import Conflict, Invalid
from mealplanner.modules.ai import store
from mealplanner.modules.ai.config import AISettings
from mealplanner.modules.ai.prompt import SYSTEM_PROMPT
from mealplanner.modules.ai.tools import TOOLS, ToolError, run_tool

log = logging.getLogger("mealplanner.ai")
if not log.handlers:  # make usage lines visible under uvicorn / journalctl
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False


class AIUnavailable(Exception):
    """The AI service could not be used for this request (not configured, or an API error)."""

    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ChatResult:
    session_id: str
    turn_id: int
    reply: str
    proposal_ids: list[int]
    iterations: int
    usage: dict[str, int]
    stopped_early: bool = False


@dataclass
class _Usage:
    totals: dict[str, int] = field(default_factory=lambda: dict.fromkeys(
        ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"), 0))

    def add(self, usage: Any) -> None:
        for key in self.totals:
            self.totals[key] += int(getattr(usage, key, 0) or 0)


_session_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(session_id: str) -> threading.Lock:
    with _locks_guard:
        return _session_locks.setdefault(session_id, threading.Lock())


def _block_dict(block: Any) -> dict[str, Any]:
    # Content blocks are stored and replayed exactly as returned (thinking blocks included).
    if isinstance(block, dict):
        return block
    return block.model_dump(mode="json", exclude_none=True)


def _context_note(conn: sqlite3.Connection, session_id: str) -> tuple[str, list[int]]:
    today = dt.date.today()
    lines = [f"[App context, not typed by the person] Today is {today:%A} {today.isoformat()}."]
    decided = store.unreported_decisions(conn, session_id)
    if decided:
        lines.append("Proposal updates since your last reply: " + "; ".join(
            f"#{p['id']} {p['summary']}: {p['status'].upper()}" for p in decided) + ".")
    waiting = store.pending(conn, session_id)
    if waiting:
        lines.append("Still waiting for the person to apply or reject: " + "; ".join(
            f"#{p['id']} {p['summary']}" for p in waiting) + ".")
    return " ".join(lines), [p["id"] for p in decided]


def _unanswered_tool_results(content: list[dict], reason: str) -> list[dict]:
    """Close any tool calls left without results so the stored history stays valid."""
    return [
        {"type": "tool_result", "tool_use_id": b["id"], "content": reason, "is_error": True}
        for b in content if b.get("type") == "tool_use"
    ]


def chat(conn: sqlite3.Connection, client: Any, settings: AISettings, session_id: str | None, message: str) -> ChatResult:
    message = (message or "").strip()
    if not message:
        raise Invalid("message is empty")
    if len(message) > settings.max_message_chars:
        raise Invalid(f"message is too long ({len(message)} characters; the limit is {settings.max_message_chars})")

    if session_id:
        store.require_session(conn, session_id)
    else:
        session_id = store.create_session(conn)
    if store.turn_count(conn, session_id) >= settings.max_turns:
        raise Conflict(f"this chat has reached {settings.max_turns} messages; start a new chat")

    with _lock_for(session_id):
        return _run_turn(conn, client, settings, session_id, message)


def _run_turn(conn, client, settings: AISettings, session_id: str, message: str) -> ChatResult:
    import anthropic  # imported lazily so the app runs without the package

    history = store.load_messages(conn, session_id)
    context, reported_ids = _context_note(conn, session_id)
    new_messages: list[dict] = [{"role": "user", "content": [
        {"type": "text", "text": context},
        {"type": "text", "text": message},
    ]}]
    proposal_ids: list[int] = []
    usage = _Usage()
    iterations = 0
    stopped_early = False
    stop_reason = None
    content: list[dict] = []

    extra: dict[str, Any] = {}
    if settings.effort:
        extra["output_config"] = {"effort": settings.effort}

    try:
        while True:
            iterations += 1
            last_allowed = iterations >= settings.max_iterations
            stopped_early = stopped_early or last_allowed
            try:
                response = client.messages.create(
                    model=settings.model,
                    max_tokens=settings.max_tokens,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    # On the final allowed call, no more tools: the model must answer in text.
                    tool_choice={"type": "none"} if last_allowed else {"type": "auto"},
                    cache_control={"type": "ephemeral"},
                    messages=history + new_messages,
                    **extra,
                )
            except anthropic.AuthenticationError:
                raise AIUnavailable("AI unavailable: the server's ANTHROPIC_API_KEY was rejected.") from None
            except anthropic.PermissionDeniedError:
                raise AIUnavailable("AI unavailable: the API key isn't allowed to use this model.") from None
            except anthropic.NotFoundError:
                raise AIUnavailable(f"AI unavailable: model {settings.model!r} was not found. Check MEALPLANNER_AI_MODEL.") from None
            except anthropic.RateLimitError:
                raise AIUnavailable("The AI is busy (rate limited). Try again in a minute.", 429) from None
            except anthropic.APIStatusError as exc:
                raise AIUnavailable(f"AI service error ({exc.status_code}). Try again later.", 502) from None
            except anthropic.APIConnectionError:
                raise AIUnavailable("Can't reach the AI service. Check the server's internet connection.") from None

            usage.add(response.usage)
            stop_reason = response.stop_reason
            content = [_block_dict(b) for b in response.content]
            if stop_reason == "refusal":
                break
            new_messages.append({"role": "assistant", "content": content})

            tool_calls = [b for b in content if b.get("type") == "tool_use"]
            if stop_reason != "tool_use" or not tool_calls:
                break
            if last_allowed:
                new_messages.append({"role": "user", "content": _unanswered_tool_results(
                    content, "Not run: the step limit for this message was reached.")})
                break

            results = []
            for call in tool_calls:
                try:
                    output, proposal_id = run_tool(conn, session_id, call["name"], call.get("input") or {})
                    if proposal_id is not None:
                        proposal_ids.append(proposal_id)
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": json.dumps(output)})
                except ToolError as exc:
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": str(exc), "is_error": True})
            new_messages.append({"role": "user", "content": results})
    except BaseException:
        store.discard_proposals(conn, proposal_ids)  # the person never saw them
        raise

    text = "\n\n".join(b["text"] for b in content if b.get("type") == "text" and b.get("text")).strip()
    if stop_reason == "refusal":
        reply = "The AI declined to help with that request."
        new_messages = []  # keep the declined exchange out of the history
    else:
        reply = text or ("I made the proposals below." if proposal_ids else "I don't have a reply for that.")
        if stop_reason == "max_tokens":
            reply += " (Reply cut short.)"
            if any(b.get("type") == "tool_use" for b in content):
                new_messages.append({"role": "user", "content": _unanswered_tool_results(content, "Not run: reply was cut short.")})
        if stopped_early:
            reply += f" (Stopped after {settings.max_iterations} steps; ask me to continue if needed.)"

    with transaction(conn):
        store.append_messages(conn, session_id, new_messages)
        turn_id = store.add_turn(conn, session_id, user_text=message, reply=reply, model=settings.model,
                                 iterations=iterations, usage=usage.totals)
        store.attach_proposals_to_turn(conn, proposal_ids, turn_id)
        store.mark_reported(conn, reported_ids if new_messages else [])

    t = usage.totals
    log.info(
        "chat session=%s turn=%d model=%s iterations=%d stop=%s input_tokens=%d output_tokens=%d "
        "cache_read=%d cache_write=%d proposals=%d",
        session_id[:8], turn_id, settings.model, iterations, stop_reason, t["input_tokens"], t["output_tokens"],
        t["cache_read_input_tokens"], t["cache_creation_input_tokens"], len(proposal_ids),
    )
    return ChatResult(session_id, turn_id, reply, proposal_ids, iterations, dict(t), stopped_early)
