"""End-to-end test for the path-ambiguity escalation behaviour.

When the planner sees a query like 'Ravi's tickets' with no qualifier
verb, both `sys_user.incidentsReported` (caller side) and
`sys_user.incidentsAssigned` (assignee side) are 1-hop shortest chains
from sys_user to incident. The new architecture does NOT guess — it
sets intent=ambiguous and surfaces a clarification.

This replaces the old role-routing logic (department-based heuristics
in the plan_generator prompt), which silently produced wrong answers
for IT-side users whose data role was assignee.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedLLM


def test_bare_ravis_tickets_returns_ambiguous_with_clarification(
    client: TestClient, llms: dict[str, ScriptedLLM],
) -> None:
    llms["planner"].queue_tool(
        {
            "intent": "ambiguous",
            "reasoning": (
                "'Ravi's tickets' is ambiguous between tickets he raised "
                "(caller side) and tickets currently assigned to him "
                "(assignee side); both are 1-hop shortest chains."
            ),
            "operations": [],
            "confidence": 0.5,
            "clarification_needed": (
                "Do you mean tickets Ravi raised, or tickets currently "
                "assigned to Ravi?"
            ),
        }
    )

    r = client.post("/v1/query", json={"query": "Ravi's tickets"})
    assert r.status_code == 200
    body = r.json()

    assert body["intent"] == "ambiguous"
    assert "raised" in body["answer"].lower()
    assert "assigned" in body["answer"].lower()
    # No execution happened (zero ops).
    assert body.get("data") is None
    # Responder NOT invoked — the answer IS the clarification.
    assert llms["response"].text_calls == []


def test_qualified_assigned_to_ravi_is_unambiguous(
    client: TestClient, llms: dict[str, ScriptedLLM],
) -> None:
    """'Tickets assigned to Ravi' should NOT be ambiguous — the verb
    'assigned to' uniquely picks the incidentsAssigned chain. The
    planner emits a real plan and the executor runs."""
    llms["planner"].queue_tool(
        {
            "intent": "lookup",
            "reasoning": (
                "User said 'assigned to', which uniquely picks "
                "sys_user.incidentsAssigned."
            ),
            "operations": [
                {
                    "op": "find",
                    "id": "u",
                    "entity": "sys_user",
                    "filters": [
                        {"field": "name", "operator": "contains", "value": "Ravi"}
                    ],
                },
                {
                    "op": "traverse",
                    "id": "inc",
                    "from": "$u",
                    "to_entity": "incident",
                    "path": ["sys_user.incidentsAssigned"],
                },
                {
                    "op": "resolve",
                    "id": "out",
                    "source": "$inc",
                    "fields": ["number", "state"],
                },
            ],
            "output_spec": {"format": "list", "final_var": "out"},
            "confidence": 0.95,
        }
    )
    llms["response"].queue_text("Ravi is assigned to INC0012345 and INC0012348.")

    r = client.post("/v1/query", json={"query": "Tickets assigned to Ravi"})
    assert r.status_code == 200
    body = r.json()
    assert body["intent"] == "lookup"
    nums = {row["number"] for row in body["data"]}
    assert {"INC0012345", "INC0012348"} <= nums
