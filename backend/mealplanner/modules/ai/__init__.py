"""AI assistant: a chat endpoint running a Claude tool-use loop on the server.

Read tools run immediately; write tools only create proposals that the person
applies or rejects in the app. Without ANTHROPIC_API_KEY (or without the
`anthropic` package) chat reports "AI not configured" and nothing else in the
app is affected; applying or rejecting proposals never needs the API.
"""

from __future__ import annotations

import functools
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mealplanner.core.deps import Conn
from mealplanner.core.module import Module
from mealplanner.modules.ai import store
from mealplanner.modules.ai.config import api_key, load_settings
from mealplanner.modules.ai.engine import AIUnavailable, chat
from mealplanner.modules.ai.tools import apply_proposal, reject_proposal


def _unavailable_reason() -> str | None:
    if api_key() is None:
        return "AI not configured: set ANTHROPIC_API_KEY on the server."
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "AI not configured: the 'anthropic' package is not installed on the server."
    return None


@functools.lru_cache(maxsize=1)
def _client_for(key: str) -> Any:
    import anthropic

    return anthropic.Anthropic(api_key=key, timeout=60.0, max_retries=2)


def get_ai_client() -> Any | None:
    """The Claude client, or None when AI isn't configured. Tests override this dependency."""
    if _unavailable_reason():
        return None
    return _client_for(api_key())


AIClient = Annotated[Any, Depends(get_ai_client)]


class ChatIn(BaseModel):
    session_id: str | None = None
    message: str


router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status")
def status(client: AIClient) -> dict:
    settings = load_settings()
    reason = None if client is not None else (_unavailable_reason() or "AI not configured.")
    return {
        "available": client is not None,
        "reason": reason,
        "model": settings.model,
        "max_message_chars": settings.max_message_chars,
    }


@router.post("/chat")
def post_chat(data: ChatIn, conn: Conn, client: AIClient) -> dict:
    if client is None:
        raise HTTPException(status_code=503, detail=_unavailable_reason() or "AI not configured.")
    try:
        result = chat(conn, client, load_settings(), data.session_id, data.message)
    except AIUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    return {
        "session_id": result.session_id,
        "turn_id": result.turn_id,
        "reply": result.reply,
        "proposals": [store.proposal_view(p) for p in store.proposals_for(conn, ids=result.proposal_ids)],
        "stopped_early": result.stopped_early,
        "iterations": result.iterations,
        "usage": result.usage,
    }


@router.get("/sessions/{session_id}")
def get_session(session_id: str, conn: Conn) -> dict:
    store.require_session(conn, session_id)
    return {
        "session_id": session_id,
        "turns": [
            {
                "id": t["id"],
                "user": t["user_text"],
                "reply": t["reply"],
                "created_at": t["created_at"],
                "proposals": [store.proposal_view(p) for p in store.proposals_for(conn, turn_id=t["id"])],
            }
            for t in store.list_turns(conn, session_id)
        ],
    }


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: int, conn: Conn) -> dict:
    return store.proposal_view(store.get_proposal(conn, proposal_id))


@router.post("/proposals/{proposal_id}/apply")
def post_apply(proposal_id: int, conn: Conn) -> dict:
    proposal, result = apply_proposal(conn, proposal_id)
    return {"proposal": proposal, "result": result}


@router.post("/proposals/{proposal_id}/reject")
def post_reject(proposal_id: int, conn: Conn) -> dict:
    return {"proposal": reject_proposal(conn, proposal_id)}


module = Module(name="ai", router=router, migrations=store.MIGRATIONS, description="Claude assistant with confirm-before-write proposals")
