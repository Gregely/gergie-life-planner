"""Persistence for chat sessions, the model-facing message history, and proposals.

The message history is append-only: each chat turn adds messages and never
rewrites earlier ones (the API requires earlier turns, including thinking
blocks, to be replayed unchanged).
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import uuid
from typing import Any

from mealplanner.core.db import Migration
from mealplanner.core.errors import NotFound

MIGRATIONS = [
    Migration(
        1,
        """
        CREATE TABLE ai_sessions (
            id         TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );

        -- Exactly what is sent to the model, in order (JSON content blocks).
        CREATE TABLE ai_messages (
            id         INTEGER PRIMARY KEY,
            session_id TEXT    NOT NULL REFERENCES ai_sessions(id) ON DELETE CASCADE,
            role       TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
            content    TEXT    NOT NULL
        );
        CREATE INDEX ai_messages_session ON ai_messages(session_id, id);

        -- What the person sees: one row per message they sent, with token usage.
        CREATE TABLE ai_turns (
            id                INTEGER PRIMARY KEY,
            session_id        TEXT    NOT NULL REFERENCES ai_sessions(id) ON DELETE CASCADE,
            user_text         TEXT    NOT NULL,
            reply             TEXT    NOT NULL,
            model             TEXT    NOT NULL,
            iterations        INTEGER NOT NULL,
            input_tokens      INTEGER NOT NULL,
            output_tokens     INTEGER NOT NULL,
            cache_read_tokens INTEGER NOT NULL,
            created_at        TEXT    NOT NULL
        );
        CREATE INDEX ai_turns_session ON ai_turns(session_id, id);

        -- Write actions the model wants to take. Nothing changes until applied.
        CREATE TABLE ai_proposals (
            id         INTEGER PRIMARY KEY,
            session_id TEXT    NOT NULL REFERENCES ai_sessions(id) ON DELETE CASCADE,
            turn_id    INTEGER REFERENCES ai_turns(id) ON DELETE SET NULL,
            tool       TEXT    NOT NULL,
            input      TEXT    NOT NULL,
            summary    TEXT    NOT NULL,
            details    TEXT    NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'applied', 'rejected', 'discarded')),
            result     TEXT,
            reported   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT    NOT NULL,
            decided_at TEXT
        );
        CREATE INDEX ai_proposals_session ON ai_proposals(session_id, id);
        """,
    ),
]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# --- sessions and history -------------------------------------------------------


def create_session(conn: sqlite3.Connection) -> str:
    session_id = uuid.uuid4().hex
    conn.execute("INSERT INTO ai_sessions (id, created_at) VALUES (?, ?)", (session_id, now()))
    return session_id


def session_exists(conn: sqlite3.Connection, session_id: str) -> bool:
    return conn.execute("SELECT 1 FROM ai_sessions WHERE id = ?", (session_id,)).fetchone() is not None


def require_session(conn: sqlite3.Connection, session_id: str) -> None:
    if not session_exists(conn, session_id):
        raise NotFound(f"chat session {session_id} not found")


def load_messages(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT role, content FROM ai_messages WHERE session_id = ? ORDER BY id", (session_id,))
    return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]


def append_messages(conn: sqlite3.Connection, session_id: str, messages: list[dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT INTO ai_messages (session_id, role, content) VALUES (?, ?, ?)",
        [(session_id, m["role"], json.dumps(m["content"])) for m in messages],
    )


def turn_count(conn: sqlite3.Connection, session_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM ai_turns WHERE session_id = ?", (session_id,)).fetchone()[0]


def add_turn(conn: sqlite3.Connection, session_id: str, *, user_text: str, reply: str, model: str,
             iterations: int, usage: dict[str, int]) -> int:
    cur = conn.execute(
        "INSERT INTO ai_turns (session_id, user_text, reply, model, iterations, input_tokens, output_tokens, "
        "cache_read_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, user_text, reply, model, iterations, usage["input_tokens"], usage["output_tokens"],
         usage["cache_read_input_tokens"], now()),
    )
    return cur.lastrowid


def list_turns(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM ai_turns WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()


# --- proposals -------------------------------------------------------------------


def add_proposal(conn: sqlite3.Connection, session_id: str, tool: str, tool_input: dict[str, Any],
                 summary: str, details: list[str]) -> int:
    cur = conn.execute(
        "INSERT INTO ai_proposals (session_id, tool, input, summary, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, tool, json.dumps(tool_input), summary, json.dumps(details), now()),
    )
    return cur.lastrowid


def get_proposal(conn: sqlite3.Connection, proposal_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM ai_proposals WHERE id = ?", (proposal_id,)).fetchone()
    if row is None:
        raise NotFound(f"proposal {proposal_id} not found")
    return row


def proposals_for(conn: sqlite3.Connection, *, session_id: str | None = None, turn_id: int | None = None,
                  ids: list[int] | None = None) -> list[sqlite3.Row]:
    if ids is not None:
        if not ids:
            return []
        return conn.execute(
            f"SELECT * FROM ai_proposals WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id", ids
        ).fetchall()
    if turn_id is not None:
        return conn.execute("SELECT * FROM ai_proposals WHERE turn_id = ? ORDER BY id", (turn_id,)).fetchall()
    return conn.execute("SELECT * FROM ai_proposals WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()


def attach_proposals_to_turn(conn: sqlite3.Connection, ids: list[int], turn_id: int) -> None:
    conn.executemany("UPDATE ai_proposals SET turn_id = ? WHERE id = ?", [(turn_id, i) for i in ids])


def discard_proposals(conn: sqlite3.Connection, ids: list[int]) -> None:
    conn.executemany(
        "UPDATE ai_proposals SET status = 'discarded', decided_at = ? WHERE id = ? AND status = 'pending'",
        [(now(), i) for i in ids],
    )


def decide(conn: sqlite3.Connection, proposal_id: int, status: str, result: Any = None) -> None:
    conn.execute(
        "UPDATE ai_proposals SET status = ?, result = ?, decided_at = ? WHERE id = ?",
        (status, None if result is None else json.dumps(result), now(), proposal_id),
    )


def unreported_decisions(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ai_proposals WHERE session_id = ? AND status IN ('applied', 'rejected') AND reported = 0 ORDER BY id",
        (session_id,),
    ).fetchall()


def pending(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ai_proposals WHERE session_id = ? AND status = 'pending' ORDER BY id", (session_id,)
    ).fetchall()


def mark_reported(conn: sqlite3.Connection, ids: list[int]) -> None:
    conn.executemany("UPDATE ai_proposals SET reported = 1 WHERE id = ?", [(i,) for i in ids])


def proposal_view(row: sqlite3.Row) -> dict[str, Any]:
    """What the frontend shows on a proposal card."""
    return {
        "id": row["id"],
        "tool": row["tool"],
        "summary": row["summary"],
        "details": json.loads(row["details"]),
        "status": row["status"],
        "result": None if row["result"] is None else json.loads(row["result"]),
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
    }
