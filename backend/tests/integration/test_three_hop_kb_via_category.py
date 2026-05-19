"""End-to-end test for the 3-hop KB-via-category bridge.

Query: "KB articles relevant to incidents Ravi's team is handling"

Shape: a single declarative traverse op walks
sys_user -> incident -> category -> kb_knowledge, rather than the
brittle 4-op chain a non-declarative planner would produce.

Asserts: planner emits the declarative shape; graph.execute_plan walks
the chain via SchemaGraph.walk; trace records the chain end-to-end;
responder cites the right KB articles.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedLLM


def test_three_hop_kb_via_category_bridge(
    client: TestClient, llms: dict[str, ScriptedLLM],
) -> None:
    """A multi-hop walk in one declarative op."""
    llms["planner"].queue_tool(
        {
            "intent": "cross_reference",
            "reasoning": (
                "Find Ravi, then declaratively reach kb_knowledge through "
                "the team's incidents and their categories."
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
                    "id": "kb",
                    "from": "$u",
                    "to_entity": "kb_knowledge",
                    "path": [
                        "sys_user.incidentsAssigned",
                        "incident.inCategory",
                        "category.kbArticlesInCategory",
                    ],
                },
                {
                    "op": "resolve",
                    "id": "out",
                    "source": "$kb",
                    "fields": ["number", "short_description", "kb_category"],
                },
            ],
            "output_spec": {"format": "list", "final_var": "out"},
            "confidence": 0.85,
        }
    )
    llms["response"].queue_text(
        "KB0045678 (VPN troubleshooting) is relevant to the team's current "
        "Network incident, and KB0045682 (slow laptop after Windows update) "
        "is relevant to the Hardware incident."
    )

    r = client.post(
        "/v1/query",
        json={"query": "KB articles relevant to incidents Ravi's team is handling"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["intent"] == "cross_reference"

    answer = body["answer"]
    assert "KB0045678" in answer or "KB0045682" in answer

    trace = body["trace"]
    traverse_step = next(
        s for s in trace["steps"] if s["op_type"] == "traverse"
    )
    assert traverse_step["graph_traversal"] == [
        "sys_user.incidentsAssigned",
        "incident.inCategory",
        "category.kbArticlesInCategory",
    ]
    assert traverse_step["target_entity"] == "kb_knowledge"

    data = body["data"]
    assert isinstance(data, list)
    assert len(data) >= 1
    numbers = {row["number"] for row in data}
    assert numbers & {"KB0045678", "KB0045682"}, numbers


def test_three_hop_kb_via_category_with_open_state_mid_hop_filter(
    client: TestClient, llms: dict[str, ScriptedLLM],
) -> None:
    """Same walk with state=open filter on the incident hop; exercises
    filters_by_entity mid-walk."""
    llms["planner"].queue_tool(
        {
            "intent": "cross_reference",
            "reasoning": (
                "Reach kb_knowledge from Ravi, filtering incidents to open."
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
                    "id": "kb",
                    "from": "$u",
                    "to_entity": "kb_knowledge",
                    "path": [
                        "sys_user.incidentsAssigned",
                        "incident.inCategory",
                        "category.kbArticlesInCategory",
                    ],
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
                    "source": "$kb",
                    "fields": ["number", "kb_category"],
                },
            ],
            "output_spec": {"format": "list", "final_var": "out"},
            "confidence": 0.85,
        }
    )
    llms["response"].queue_text("KB0045678 covers the open Network case.")

    r = client.post(
        "/v1/query",
        json={"query": "KB articles for open incidents Ravi's team is handling"},
    )
    assert r.status_code == 200
    body = r.json()

    traverse_step = next(
        s for s in body["trace"]["steps"] if s["op_type"] == "traverse"
    )
    assert "incident" in traverse_step.get("hops_filtered", [])
