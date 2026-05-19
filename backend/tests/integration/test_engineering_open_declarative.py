"""End-to-end test for the declarative cross-reference shape with a
mid-hop filter.

Query: 'Open tickets raised by people in Engineering'

This is a 2-step plan in the new architecture:
  1. find sys_user where department=Engineering
  2. traverse to incident via [sys_user.incidentsReported] with
     filters_by_entity={incident: state in [...open]}
  3. resolve

The legacy planner would have emitted 3-4 ops with a separate post-
traverse filter; the declarative form expresses the same intent in one
clean step. The validator confirms `path` is a shortest chain
(sys_user -> incident is length 1) and that `filters_by_entity[incident]`
targets an entity actually in the chain.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedLLM


def test_engineering_open_tickets_via_declarative_traverse(
    client: TestClient, llms: dict[str, ScriptedLLM],
) -> None:
    llms["planner"].queue_tool(
        {
            "intent": "cross_reference",
            "reasoning": (
                "Find Engineering users, declaratively reach incident with "
                "an open-state filter on the incident hop."
            ),
            "operations": [
                {
                    "op": "find",
                    "id": "u",
                    "entity": "sys_user",
                    "filters": [
                        {"field": "department", "operator": "eq", "value": "Engineering"}
                    ],
                },
                {
                    "op": "traverse",
                    "id": "inc",
                    "from": "$u",
                    "to_entity": "incident",
                    "path": ["sys_user.incidentsReported"],
                    "filters_by_entity": {
                        "incident": [
                            {
                                "field": "state",
                                "operator": "in",
                                "value": ["New", "In Progress", "On Hold"],
                            }
                        ]
                    },
                },
                {
                    "op": "resolve",
                    "id": "out",
                    "source": "$inc",
                    "fields": ["number", "state", "priority"],
                },
            ],
            "output_spec": {"format": "list", "final_var": "out"},
            "confidence": 0.92,
        }
    )
    llms["response"].queue_text(
        "Engineering employees raised INC0012345 (In Progress) and "
        "INC0012349 (In Progress), both still open."
    )

    r = client.post(
        "/v1/query",
        json={"query": "Open tickets raised by people in Engineering"},
    )
    assert r.status_code == 200
    body = r.json()

    # All returned incidents must be in an open state.
    for row in body["data"]:
        assert row["state"] in {"New", "In Progress", "On Hold"}, row

    # The trace records the filter firing on the incident hop.
    traverse_step = next(
        s for s in body["trace"]["steps"] if s["op_type"] == "traverse"
    )
    assert "incident" in traverse_step["hops_filtered"]
    assert traverse_step["graph_traversal"] == ["sys_user.incidentsReported"]
