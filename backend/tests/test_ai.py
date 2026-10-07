"""AI module with the Claude client mocked: no real API calls are made."""

import copy
import json
import logging
import sqlite3

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from mealplanner.modules.ai import get_ai_client
from mealplanner.modules.ai.tools import normalise_name


# --- fake Claude client ---------------------------------------------------------


def text(t):
    return {"type": "text", "text": t}


def tool(name, tool_input=None, id=None):
    return {"type": "tool_use", "id": id or f"tu_{name}", "name": name, "input": tool_input or {}}


THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}


def reply(*blocks, stop=None, input_tokens=100, output_tokens=20, cache_read=0):
    if stop is None:
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return Message.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": list(blocks), "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": 0},
    })


class FakeClient:
    """Stands in for anthropic.Anthropic: returns scripted replies and records every request."""

    def __init__(self, script):
        self.script = list(script) if isinstance(script, list) else script
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if callable(self.script):
            return self.script(kwargs)
        assert self.script, "the model was called more times than scripted"
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def fake(client, monkeypatch):
    """Install a fake client; tests set fake.script before chatting."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-never-leaves-server")
    for name in ("MEALPLANNER_AI_MODEL", "MEALPLANNER_AI_MAX_ITERATIONS", "MEALPLANNER_AI_MAX_MESSAGE_CHARS",
                 "MEALPLANNER_AI_MAX_TURNS", "MEALPLANNER_AI_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    f = FakeClient([])
    client.app.dependency_overrides[get_ai_client] = lambda: f
    yield f
    client.app.dependency_overrides.clear()


def say(client, message, session_id=None, status=200):
    resp = client.post("/api/ai/chat", json={"session_id": session_id, "message": message})
    assert resp.status_code == status, resp.text
    return resp.json()


def tool_results(call):
    """tool_result blocks in the last message of a recorded request."""
    return [b for b in call["messages"][-1]["content"] if b.get("type") == "tool_result"]


# --- not configured -------------------------------------------------------------


def test_missing_key_reports_not_configured_and_app_still_works(client, no_key):
    status = client.get("/api/ai/status").json()
    assert status["available"] is False
    assert "ANTHROPIC_API_KEY" in status["reason"]

    resp = client.post("/api/ai/chat", json={"message": "hi"})
    assert resp.status_code == 503
    assert "AI not configured" in resp.json()["detail"]

    assert client.post("/api/ingredients", json={"name": "Rice", "unit": "g"}).status_code == 201
    assert client.get("/api/shopping-list", params={"start": "2026-10-05", "end": "2026-10-11"}).status_code == 200


def test_proposals_can_be_applied_after_ai_goes_away(client, fake, monkeypatch):
    fake.script = [reply(tool("create_ingredient", {"name": "Rice", "unit": "g"})), reply(text("Proposed."))]
    [proposal] = say(client, "add rice")["proposals"]
    client.app.dependency_overrides.clear()
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert client.post(f"/api/ai/proposals/{proposal['id']}/apply").status_code == 200
    assert [i["name"] for i in client.get("/api/ingredients").json()] == ["Rice"]


def test_api_key_never_reaches_the_frontend(client, fake):
    fake.script = [reply(text("Hello"))]
    secret = "sk-ant-test-key-never-leaves-server"
    responses = [
        client.get("/api/ai/status"), client.post("/api/ai/chat", json={"message": "hi"}),
        client.get("/"), client.get("/app.js"),
    ]
    for resp in responses:
        assert secret not in resp.text


# --- the tool loop ----------------------------------------------------------------


def test_read_tools_run_and_loop_until_text(client, fake):
    client.post("/api/ingredients", json={"name": "Onion", "unit": "count"})
    fake.script = [
        reply(THINKING, tool("list_ingredients")),
        reply(text("You have one ingredient: Onion.")),
    ]
    out = say(client, "what ingredients do I have?")
    assert out["reply"] == "You have one ingredient: Onion."
    assert out["proposals"] == [] and out["iterations"] == 2

    first, second = fake.calls
    assert first["model"] == "claude-haiku-5-5"
    assert "tight budget" in first["system"]
    assert {t["name"] for t in first["tools"]} >= {"list_ingredients", "create_recipe", "mark_eaten"}
    assert "temperature" not in first and first["tool_choice"] == {"type": "auto"}
    # The assistant turn is replayed unchanged, thinking block (with its signature) included.
    assert second["messages"][1] == {"role": "assistant", "content": [THINKING, tool("list_ingredients")]}
    [result] = tool_results(second)
    assert result["tool_use_id"] == "tu_list_ingredients" and "is_error" not in result
    assert json.loads(result["content"])[0]["name"] == "Onion"


def test_parallel_tool_calls_answered_in_one_message(client, fake):
    fake.script = [
        reply(tool("get_pantry", id="a"), tool("get_plan", {"start": "2026-10-05", "end": "2026-10-11"}, id="b")),
        reply(text("Done.")),
    ]
    say(client, "status?")
    assert [r["tool_use_id"] for r in tool_results(fake.calls[1])] == ["a", "b"]


def test_model_comes_from_env(client, fake, monkeypatch):
    monkeypatch.setenv("MEALPLANNER_AI_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("MEALPLANNER_AI_EFFORT", "low")
    fake.script = [reply(text("ok"))]
    say(client, "hi")
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"
    assert fake.calls[0]["output_config"] == {"effort": "low"}
    assert client.get("/api/ai/status").json()["model"] == "claude-sonnet-5-5"


def test_tool_errors_go_back_to_the_model(client, fake):
    client.post("/api/ingredients", json={"name": "Onion", "unit": "count"})
    fake.script = [
        reply(tool("create_ingredient", {"name": "onions", "unit": "count"})),
        reply(tool("get_plan", {"start": "next week", "end": "2026-10-11"})),
        reply(text("Onion already exists.")),
    ]
    out = say(client, "add onions")
    assert out["proposals"] == []
    [dup] = tool_results(fake.calls[1])
    assert dup["is_error"] is True and "already exists" in dup["content"] and "Onion" in dup["content"]
    [bad_date] = tool_results(fake.calls[2])
    assert bad_date["is_error"] is True and "YYYY-MM-DD" in bad_date["content"]


def test_iteration_cap_forces_a_text_answer(client, fake, monkeypatch):
    monkeypatch.setenv("MEALPLANNER_AI_MAX_ITERATIONS", "3")
    fake.script = lambda kw: reply(text("Here is what I found.")) if kw["tool_choice"] == {"type": "none"} \
        else reply(tool("get_pantry", id=f"t{len(fake.calls)}"))
    out = say(client, "loop forever")
    assert len(fake.calls) == 3
    assert [c["tool_choice"]["type"] for c in fake.calls] == ["auto", "auto", "none"]
    assert out["stopped_early"] is True and "Stopped after 3 steps" in out["reply"]


def test_iteration_cap_holds_even_if_model_keeps_calling_tools(client, fake, monkeypatch):
    monkeypatch.setenv("MEALPLANNER_AI_MAX_ITERATIONS", "2")
    fake.script = lambda kw: reply(tool("get_pantry", id=f"t{len(fake.calls)}"))
    out = say(client, "loop forever")
    assert len(fake.calls) == 2 and out["stopped_early"] is True
    # The stored history stays valid: every tool call has a result.
    fake.script = [reply(text("ok"))]
    say(client, "again", out["session_id"])
    history = fake.calls[-1]["messages"]
    used = {b["id"] for m in history if m["role"] == "assistant" for b in m["content"] if b["type"] == "tool_use"}
    answered = {b["tool_use_id"] for m in history if m["role"] == "user" for b in m["content"] if b.get("type") == "tool_result"}
    assert used == answered


# --- proposals ------------------------------------------------------------------


def test_write_tools_only_propose_until_applied(client, fake):
    fake.script = [
        reply(tool("create_ingredient", {"name": "Mince", "unit": "g", "category": "Meat"})),
        reply(text("I've proposed adding Mince. Tap Apply to confirm.")),
    ]
    out = say(client, "add mince")
    [proposal] = out["proposals"]
    assert proposal["status"] == "pending" and proposal["summary"] == 'Add ingredient "Mince"'
    assert "No price" in proposal["details"]
    assert client.get("/api/ingredients").json() == []  # nothing written yet
    result = json.loads(tool_results(fake.calls[1])[0]["content"])
    assert result["proposal_id"] == proposal["id"] and "NOT applied" in result["status"]

    applied = client.post(f"/api/ai/proposals/{proposal['id']}/apply").json()
    assert applied["proposal"]["status"] == "applied"
    assert [i["name"] for i in client.get("/api/ingredients").json()] == ["Mince"]
    assert client.post(f"/api/ai/proposals/{proposal['id']}/apply").status_code == 409
    assert client.post(f"/api/ai/proposals/{proposal['id']}/reject").status_code == 409


def test_rejected_proposal_is_never_applied(client, fake):
    fake.script = [reply(tool("create_ingredient", {"name": "Mince", "unit": "g"})), reply(text("ok"))]
    [proposal] = say(client, "add mince")["proposals"]
    assert client.post(f"/api/ai/proposals/{proposal['id']}/reject").json()["proposal"]["status"] == "rejected"
    assert client.post(f"/api/ai/proposals/{proposal['id']}/apply").status_code == 409
    assert client.get("/api/ingredients").json() == []


def test_mark_eaten_is_only_a_proposal(api, client, fake):
    mince = api.ingredient("Mince")
    entry = api.plan("2026-10-07", "dinner", api.recipe("Chilli", {mince: 500}, servings=4))
    api.stock(mince, 100)
    fake.script = [reply(tool("mark_eaten", {"plan_id": entry})), reply(text("Proposed."))]
    [proposal] = say(client, "I ate dinner")["proposals"]
    assert "Takes its ingredients out of the pantry" in proposal["details"]
    assert api.pantry() == {mince: 100}
    assert client.get(f"/api/plan/{entry}").json()["eaten_at"] is None

    result = client.post(f"/api/ai/proposals/{proposal['id']}/apply").json()["result"]
    assert api.pantry() == {mince: 0}
    assert result["shortfalls"][0]["shortfall"] == 25  # same backend maths as the plan grid


def test_recipe_with_new_ingredients_applies_in_order(api, client, fake):
    rice = api.ingredient("Rice")
    fake.script = [
        reply(tool("create_ingredient", {"name": "Saffron", "unit": "g"}, id="a")),
        reply(tool("create_recipe", {"name": "Paella", "servings": 4, "items": [
            {"ingredient_id": rice, "quantity": 300}, {"ingredient_name": "saffron", "quantity": 1}]}, id="b")),
        reply(tool("plan_meal", {"date": "2026-10-09", "slot": "dinner", "recipe_name": "Paella", "portions": 2}, id="c")),
        reply(text("Proposed saffron, the recipe and Friday's dinner.")),
    ]
    ingredient_p, recipe_p, plan_p = say(client, "plan paella friday")["proposals"]
    assert "Saffron: 1 g (new ingredient)" in recipe_p["details"]

    # Out of order: the recipe needs Saffron, which doesn't exist yet -> nothing happens.
    resp = client.post(f"/api/ai/proposals/{recipe_p['id']}/apply")
    assert resp.status_code == 409 and "apply the proposal that adds it first" in resp.json()["detail"]
    assert client.get("/api/recipes").json() == []
    assert client.get(f"/api/ai/proposals/{recipe_p['id']}").json()["status"] == "pending"

    for p in (ingredient_p, recipe_p, plan_p):
        assert client.post(f"/api/ai/proposals/{p['id']}/apply").status_code == 200, p
    [entry] = client.get("/api/plan", params={"start": "2026-10-09", "end": "2026-10-09"}).json()
    assert entry["recipe_name"] == "Paella" and entry["portions"] == 2


def test_plan_meal_replaces_the_slot(api, client, fake):
    old = api.recipe("Toast", {})
    new = api.recipe("Porridge", {})
    api.plan("2026-10-07", "breakfast", old)
    fake.script = [reply(tool("plan_meal", {"date": "2026-10-07", "slot": "breakfast", "recipe_id": new})), reply(text("ok"))]
    [p] = say(client, "porridge instead")["proposals"]
    assert 'Replaces "Toast"' in p["details"]
    client.post(f"/api/ai/proposals/{p['id']}/apply")
    [entry] = client.get("/api/plan", params={"start": "2026-10-07", "end": "2026-10-07"}).json()
    assert entry["recipe_name"] == "Porridge"


def test_record_bought_proposal_uses_list_amount(api, client, fake):
    mince = api.ingredient("Mince")
    api.plan("2026-10-07", "dinner", api.recipe("Chilli", {mince: 500}, servings=4), portions=2)
    fake.script = [reply(tool("record_bought", {"start": "2026-10-05", "end": "2026-10-11",
                                                 "items": [{"ingredient_id": mince}]})), reply(text("ok"))]
    [p] = say(client, "bought the mince")["proposals"]
    assert p["details"] == ["Mince: 250 g (amount on the list)"]
    assert api.pantry() == {}
    client.post(f"/api/ai/proposals/{p['id']}/apply")
    assert api.pantry() == {mince: 250}


def test_apply_is_atomic_when_one_item_fails(api, client, fake):
    rice, beans = api.ingredient("Rice"), api.ingredient("Beans")
    fake.script = [reply(tool("record_bought", {"start": "2026-10-05", "end": "2026-10-11", "items": [
        {"ingredient_id": rice, "quantity": 500}, {"ingredient_id": beans, "quantity": 400}]})), reply(text("ok"))]
    [p] = say(client, "bought rice and beans")["proposals"]
    client.delete(f"/api/ingredients/{beans}")  # one item becomes impossible
    assert client.post(f"/api/ai/proposals/{p['id']}/apply").status_code == 404
    assert api.pantry() == {}  # the rice was not added either
    assert client.get(f"/api/ai/proposals/{p['id']}").json()["status"] == "pending"


def test_apply_rolls_back_nested_transaction_and_status(api, client, fake, db_path):
    oats = api.ingredient("Oats")
    entry = api.plan("2026-10-07", "breakfast", api.recipe("Porridge", {oats: 50}))
    api.stock(oats, 500)
    fake.script = [reply(tool("mark_eaten", {"plan_id": entry})), reply(text("ok"))]
    [p] = say(client, "ate breakfast")["proposals"]
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE consumption_log")  # fails after the pantry update inside mark_eaten
    conn.close()
    with pytest.raises(sqlite3.OperationalError):
        client.post(f"/api/ai/proposals/{p['id']}/apply")
    assert api.pantry() == {oats: 500}
    assert client.get(f"/api/plan/{entry}").json()["eaten_at"] is None
    assert client.get(f"/api/ai/proposals/{p['id']}").json()["status"] == "pending"


def test_unknown_proposal(client):
    assert client.post("/api/ai/proposals/999/apply").status_code == 404
    assert client.post("/api/ai/proposals/999/reject").status_code == 404


# --- sessions, history, limits, errors, usage ----------------------------------------


def test_history_is_append_only_and_reports_decisions(client, fake):
    fake.script = [reply(THINKING, tool("create_ingredient", {"name": "Leeks", "unit": "count"})), reply(text("Proposed leeks."))]
    first = say(client, "add leeks")
    session = first["session_id"]
    first_turn_messages = fake.calls[-1]["messages"] + [{"role": "assistant", "content": [text("Proposed leeks.")]}]
    client.post(f"/api/ai/proposals/{first['proposals'][0]['id']}/apply")

    fake.script = [reply(text("Great."))]
    say(client, "thanks", session)
    sent = fake.calls[-1]["messages"]
    assert sent[: len(first_turn_messages)] == first_turn_messages  # earlier turns replayed byte-for-byte
    note = sent[-1]["content"][0]["text"]
    assert 'Add ingredient "Leeks": APPLIED' in note and "Today is" in note
    assert sent[-1]["content"][1] == text("thanks")

    fake.script = [reply(text("ok"))]
    say(client, "anything else?", session)
    assert "APPLIED" not in fake.calls[-1]["messages"][-1]["content"][0]["text"]  # reported once


def test_session_history_endpoint(client, fake):
    fake.script = [reply(tool("create_ingredient", {"name": "Kale", "unit": "g"})), reply(text("Proposed kale."))]
    out = say(client, "add kale")
    client.post(f"/api/ai/proposals/{out['proposals'][0]['id']}/reject")
    history = client.get(f"/api/ai/sessions/{out['session_id']}").json()
    [turn] = history["turns"]
    assert (turn["user"], turn["reply"]) == ("add kale", "Proposed kale.")
    assert turn["proposals"][0]["status"] == "rejected"
    assert client.get("/api/ai/sessions/nope").status_code == 404
    assert client.post("/api/ai/chat", json={"session_id": "nope", "message": "hi"}).status_code == 404


def test_message_length_limit(client, fake, monkeypatch):
    monkeypatch.setenv("MEALPLANNER_AI_MAX_MESSAGE_CHARS", "10")
    resp = client.post("/api/ai/chat", json={"message": "x" * 11})
    assert resp.status_code == 422 and "too long" in resp.json()["detail"]
    assert client.post("/api/ai/chat", json={"message": "   "}).status_code == 422
    assert fake.calls == []


def test_turn_limit_per_session(client, fake, monkeypatch):
    monkeypatch.setenv("MEALPLANNER_AI_MAX_TURNS", "1")
    fake.script = [reply(text("one"))]
    session = say(client, "first")["session_id"]
    resp = client.post("/api/ai/chat", json={"session_id": session, "message": "second"})
    assert resp.status_code == 409 and "start a new chat" in resp.json()["detail"]


_REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize("error, status, words", [
    (anthropic.AuthenticationError("bad key", response=httpx2.Response(401, request=_REQ), body=None), 503, "API_KEY was rejected"),
    (anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=_REQ), body=None), 429, "busy"),
    (anthropic.InternalServerError("boom", response=httpx2.Response(500, request=_REQ), body=None), 502, "AI service error"),
    (anthropic.APIConnectionError(request=_REQ), 503, "Can't reach"),
])
def test_api_errors_are_clear_and_leave_no_trace(client, fake, db_path, error, status, words):
    fake.script = [reply(tool("create_ingredient", {"name": "Rice", "unit": "g"})), error]
    resp = client.post("/api/ai/chat", json={"message": "add rice"})
    assert resp.status_code == status and words in resp.json()["detail"]
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT status FROM ai_proposals").fetchall() == [("discarded",)]
    assert conn.execute("SELECT COUNT(*) FROM ai_turns").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ai_messages").fetchone()[0] == 0
    conn.close()


def test_refusal_is_reported_and_kept_out_of_history(client, fake):
    fake.script = [reply(stop="refusal")]
    out = say(client, "something odd")
    assert "declined" in out["reply"]
    fake.script = [reply(text("hi"))]
    say(client, "hello", out["session_id"])
    assert len(fake.calls[-1]["messages"]) == 1  # only the new message


def test_token_usage_logged_and_stored(client, fake, db_path):
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger("mealplanner.ai")
    logger.addHandler(handler)
    try:
        fake.script = [reply(tool("get_pantry"), input_tokens=1200, output_tokens=40, cache_read=1000),
                       reply(text("Empty."), input_tokens=1300, output_tokens=15)]
        out = say(client, "pantry?")
    finally:
        logger.removeHandler(handler)
    assert out["usage"]["input_tokens"] == 2500 and out["usage"]["output_tokens"] == 55
    [line] = [r.getMessage() for r in records]
    assert "input_tokens=2500" in line and "output_tokens=55" in line and "cache_read=1000" in line
    row = sqlite3.connect(db_path).execute("SELECT input_tokens, output_tokens, iterations FROM ai_turns").fetchone()
    assert row == (2500, 55, 2)


def test_normalise_name():
    assert normalise_name("Onions") == normalise_name("onion")
    assert normalise_name("Tomatoes") == normalise_name("tomato")
    assert normalise_name("Berries") == normalise_name("berry")
    assert normalise_name("Chopped  tomatoes!") == "chopped tomato"
    assert normalise_name("Rice") != normalise_name("Rice flour")
