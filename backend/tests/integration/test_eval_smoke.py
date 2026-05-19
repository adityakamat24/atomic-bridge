from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from eval.reporter import write_json_report, write_markdown_report
from eval.runner import (
    check_answer,
    check_guardrails,
    check_operations,
    load_queries,
    run_all,
    run_entry,
)
from tests.conftest import ScriptedLLM

QUERIES_PATH = Path(__file__).resolve().parents[2] / "eval" / "queries.yaml"


# ---------- Pure matchers ------------------------------------------------


def test_check_operations_required_subset_passes() -> None:
    assert check_operations(["find", "traverse", "resolve"], ["find", "resolve"])


def test_check_operations_missing_required_op_fails() -> None:
    assert not check_operations(["find"], ["find", "traverse"])


def test_check_operations_handles_repeats() -> None:
    """Two traverse ops required, only one in actual -> fail."""
    assert not check_operations(["find", "traverse"], ["find", "traverse", "traverse"])
    assert check_operations(
        ["find", "traverse", "traverse"], ["find", "traverse", "traverse"]
    )


def test_check_answer_must_contain_passes() -> None:
    ok, reasons = check_answer(
        "INC0012345 is in progress.", {"answer_must_contain": ["INC0012345", "in progress"]}
    )
    assert ok
    assert reasons == []


def test_check_answer_must_contain_fails() -> None:
    ok, reasons = check_answer("nothing here", {"answer_must_contain": ["INC0012345"]})
    assert not ok
    assert any("INC0012345" in r for r in reasons)


def test_check_answer_must_not_contain_blocks() -> None:
    ok, reasons = check_answer(
        "user@acme.com", {"answer_must_not_contain": ["@acme.com"]}
    )
    assert not ok


def test_check_answer_one_of_passes_on_any() -> None:
    ok, _ = check_answer("Network is the most common", {"answer_must_contain_one_of": ["Network", "Software"]})
    assert ok


def test_check_answer_one_of_fails_on_none() -> None:
    ok, _ = check_answer("nothing matches", {"answer_must_contain_one_of": ["Network", "Software"]})
    assert not ok


def test_check_answer_write_diff_passes() -> None:
    proposal = {"diff": {"state": {"from": "In Progress", "to": "Closed"}}}
    ok, _ = check_answer(
        "anything",
        {"write_diff_must_contain": {"state": {"from": "In Progress", "to": "Closed"}}},
        proposal=proposal,
    )
    assert ok


def test_check_answer_write_diff_fails() -> None:
    proposal = {"diff": {"state": {"from": "New", "to": "Closed"}}}
    ok, reasons = check_answer(
        "anything",
        {"write_diff_must_contain": {"state": {"from": "In Progress", "to": "Closed"}}},
        proposal=proposal,
    )
    assert not ok
    assert any("expected" in r for r in reasons)


def test_check_guardrails_dangling_warning_passes() -> None:
    response = {"warnings": ["dangling reference: caller_id=usr999"]}
    entry = {"expected": {"answer_must_handle_dangling": True}}
    ok, _ = check_guardrails(response, entry)
    assert ok


def test_check_guardrails_dangling_warning_missing_fails() -> None:
    response = {"warnings": []}
    entry = {"expected": {"answer_must_handle_dangling": True}}
    ok, _ = check_guardrails(response, entry)
    assert not ok


# ---------- Queries.yaml structure --------------------------------------


def test_queries_yaml_loads_44_entries() -> None:
    qs = load_queries(QUERIES_PATH)
    assert len(qs) >= 30
    # Spot check structure
    for q in qs:
        assert "id" in q
        assert "query" in q
        assert "expected" in q


# ---------- Smoke run with scripted LLMs (3 categories) -----------------


@pytest.mark.asyncio
async def test_smoke_runs_three_categories_with_scripted_llms(
    client: Any, llms: dict[str, ScriptedLLM], tmp_path: Path
) -> None:
    """Picks one entry per category, scripts the LLM responses to match
    expected, runs the runner, asserts they all pass and a report is written.
    """
    smoke = [
        # Lookup
        {
            "id": "smoke_lookup",
            "category": "lookup",
            "query": "What's the VPN incident status?",
            "expected": {
                "intent": "lookup",
                "operations_required": ["find", "resolve"],
                "answer_must_contain": ["INC0012345"],
            },
        },
        # OOS — guardrail short-circuit, no plan
        {
            "id": "smoke_oos",
            "category": "out_of_scope",
            "query": "Translate hello to French",
            "expected": {"intent": "out_of_scope"},
        },
    ]
    # Lookup: plan + response (no preprocessor LLM call in the new pipeline).
    llms["planner"].queue_tool(
        {
            "intent": "lookup",
            "reasoning": "find INC0012345 by number",
            "operations": [
                {
                    "op": "find",
                    "id": "x",
                    "entity": "incident",
                    "filters": [
                        {"field": "number", "operator": "eq", "value": "INC0012345"}
                    ],
                },
                {"op": "resolve", "id": "out", "source": "$x", "fields": ["number", "state"]},
            ],
            "output_spec": {"format": "list", "final_var": "out"},
            "confidence": 0.95,
        }
    )
    llms["response"].queue_text("INC0012345 is in progress.")
    # OOS: planner short-circuits; no responder call.
    llms["planner"].queue_tool(
        {
            "intent": "out_of_scope",
            "reasoning": "Translation request is not an ITSM query.",
            "operations": [],
            "confidence": 0.99,
        }
    )

    async def query_fn(q: str, sid: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"query": q}
        if sid:
            body["session_id"] = sid
        return dict(client.post("/v1/query", json=body).json())

    results = await run_all(smoke, query_fn)
    assert all(r.passed for r in results), [r.reasons for r in results if not r.passed]

    write_markdown_report(results, tmp_path / "smoke.md")
    write_json_report(results, tmp_path / "smoke.json")
    assert (tmp_path / "smoke.md").exists()
    assert (tmp_path / "smoke.json").exists()


# ---------- Single-entry runner exception path -------------------------


@pytest.mark.asyncio
async def test_runner_records_exception_as_failure() -> None:
    async def failing_query(q: str, sid: str | None) -> dict[str, Any]:
        raise RuntimeError("simulated crash")

    entry = {
        "id": "x",
        "category": "lookup",
        "query": "anything",
        "expected": {"intent": "lookup"},
    }
    out = await run_entry(entry, failing_query)
    assert not out.passed
    assert any("simulated crash" in r for r in out.reasons)
