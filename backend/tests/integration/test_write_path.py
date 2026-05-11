from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import ScriptedLLM


def _close_inc_response() -> tuple[dict[str, Any], dict[str, Any]]:
    pre = {
        "intent": "write_proposal",
        "rewritten_query": "close INC0012345",
        "entity_mentions": [],
        "relevant_entities": ["incident"],
    }
    plan = {
        "intent": "write_proposal",
        "reasoning": "find INC0012345 then propose Closed",
        "operations": [
            {
                "op": "find",
                "id": "inc",
                "entity": "incident",
                "filters": [
                    {"field": "number", "operator": "eq", "value": "INC0012345"}
                ],
            },
            {
                "op": "write_proposal",
                "id": "prop",
                "action": "update_incident",
                "target_var": "$inc",
                "fields": {"state": "Closed"},
            },
        ],
        "output_spec": {"format": "write_confirmation", "final_var": "prop"},
        "confidence": 0.9,
    }
    return pre, plan


def _propose_close_token(
    client: TestClient, llms: dict[str, ScriptedLLM]
) -> str:
    pre, plan = _close_inc_response()
    llms["preprocessor"].queue_tool(pre)
    llms["planner"].queue_tool(plan)
    body = client.post("/v1/query", json={"query": "Close INC0012345"}).json()
    return str(body["write_proposal"]["token"])


# ---------- Idempotency / re-confirm -------------------------------------


def test_double_confirm_returns_404(
    client: TestClient, llms: dict[str, ScriptedLLM]
) -> None:
    token = _propose_close_token(client, llms)
    first = client.post("/v1/write/confirm", json={"token": token, "confirm": True})
    assert first.status_code == 200
    second = client.post("/v1/write/confirm", json={"token": token, "confirm": True})
    assert second.status_code == 404


# ---------- TTL expiry ---------------------------------------------------


def test_expired_proposal_returns_410(
    client: TestClient, llms: dict[str, ScriptedLLM]
) -> None:
    token = _propose_close_token(client, llms)
    container = client.app.state.container
    proposal = container.approval_store._proposals[token]  # noqa: SLF001 -- test access
    proposal.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    r = client.post("/v1/write/confirm", json={"token": token, "confirm": True})
    assert r.status_code in (404, 410)  # 410 if get() returned it; 404 if get() purged it
    if r.status_code == 410:
        assert "expired" in r.json()["detail"].lower()


# ---------- Optimistic lock --------------------------------------------


def test_lock_conflict_returns_409(
    client: TestClient, llms: dict[str, ScriptedLLM]
) -> None:
    token = _propose_close_token(client, llms)
    container = client.app.state.container

    # Mutate the target out-of-band so sys_updated_on changes.
    proposal = container.approval_store._proposals[token]  # noqa: SLF001
    target_id = proposal.target_sys_id
    container.store.update("incident", target_id, {"short_description": "out-of-band edit"})

    r = client.post("/v1/write/confirm", json={"token": token, "confirm": True})
    assert r.status_code == 409
    assert "modified" in r.json()["detail"].lower()


# ---------- Cancel --------------------------------------------------------


def test_cancel_does_not_mutate(
    client: TestClient, llms: dict[str, ScriptedLLM]
) -> None:
    token = _propose_close_token(client, llms)
    container = client.app.state.container
    proposal = container.approval_store._proposals[token]  # noqa: SLF001
    target_id = proposal.target_sys_id
    before = container.store.get("incident", target_id)["state"]
    r = client.post("/v1/write/confirm", json={"token": token, "confirm": False})
    assert r.json()["status"] == "cancelled"
    after = container.store.get("incident", target_id)["state"]
    assert before == after  # state unchanged


# ---------- Create incident path ---------------------------------------


def test_create_incident_proposal_then_confirm(
    client: TestClient, llms: dict[str, ScriptedLLM]
) -> None:
    llms["preprocessor"].queue_tool(
        {
            "intent": "write_proposal",
            "rewritten_query": "create incident",
            "entity_mentions": [],
            "relevant_entities": ["incident"],
        }
    )
    llms["planner"].queue_tool(
        {
            "intent": "write_proposal",
            "reasoning": "create new hardware incident",
            "operations": [
                {
                    "op": "write_proposal",
                    "id": "prop",
                    "action": "create_incident",
                    "fields": {
                        "short_description": "Keyboard stopped working",
                        "priority": "High",
                        "category": "Hardware",
                        "caller_id": "usr001",
                        "assignment_group": "grp001",
                    },
                }
            ],
            "output_spec": {"format": "write_confirmation", "final_var": "prop"},
            "confidence": 0.9,
        }
    )
    body = client.post(
        "/v1/query",
        json={
            "query": "Create a high-priority hardware incident: keyboard stopped working"
        },
    ).json()
    token = body["write_proposal"]["token"]
    r = client.post("/v1/write/confirm", json={"token": token, "confirm": True})
    assert r.status_code == 200
    rec = r.json()["record"]
    assert rec["short_description"] == "Keyboard stopped working"
    assert rec["priority"] == 2  # "High" -> 2
    assert rec["number"].startswith("INC")
