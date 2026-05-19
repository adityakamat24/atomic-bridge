"""End-to-end test for the reviewer's hero example.

Query: "KB articles relevant to incidents Ravi's team is handling"

Shape: 3-hop declarative walk from sys_user -> incident -> category ->
kb_knowledge. The new architecture emits this as a SINGLE traverse op
(declarative `to_entity` + `path`) rather than the brittle 4-op chain
the legacy planner would have produced.

Test asserts the full pipeline:
  * Planner emits the declarative shape.
  * graph.execute_plan walks the chain via SchemaGraph.walk.
  * Trace records the relation chain end-to-end.
  * Responder cites the right KB articles.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedLLM


def test_reviewer_hero_example_kb_via_category_bridge(
    client: TestClient, llms: dict[str, ScriptedLLM],
) -> None:
    """The full reviewer-prescribed multi-hop walk in one declarative op."""
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

    # The response cites KB articles from the categories of Ravi's
    # assigned incidents (Network + Hardware).
    answer = body["answer"]
    assert "KB0045678" in answer or "KB0045682" in answer

    # The trace records the full 3-hop chain end-to-end.
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

    # And the final data is non-empty.
    data = body["data"]
    assert isinstance(data, list)
    assert len(data) >= 1
    numbers = {row["number"] for row in data}
    assert numbers & {"KB0045678", "KB0045682"}, numbers


def test_reviewer_hero_example_with_open_state_mid_hop_filter(
    client: TestClient, llms: dict[str, ScriptedLLM],
) -> None:
    """Same walk but with state=open filter on the incident hop —
    exercises filters_by_entity. The PDF example reads as 'KB articles
    relevant to OPEN incidents the team is handling'."""
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

    # filters_by_entity fired on the incident hop.
    traverse_step = next(
        s for s in body["trace"]["steps"] if s["op_type"] == "traverse"
    )
    assert "incident" in traverse_step.get("hops_filtered", [])
