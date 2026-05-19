"""Tests for the RelationScorer, the LLM-driven path-picker invoked
when the planner leaves `path` empty and multiple simple chains exist.

It uses the response-side (quarantined) LLM client (same instance, new
role) and sees only schema metadata + the user's query, never records.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.core.schema_graph import Relation, SchemaGraph
from src.core.schema_loader import load
from src.llm.base import BaseLLMClient
from src.planner.relation_scorer import RelationScorer, ScorerChoice

REAL_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"


class _ScriptedLLM(BaseLLMClient):
    """Captures every text_complete call so tests can assert on the
    scorer's prompt structure + push canned responses."""

    def __init__(self) -> None:
        super().__init__(model="scripted-scorer")
        self.next_text: str = '{"choice": 0, "confidence": 0.9, "reasoning": "default"}'
        self.text_calls: list[dict[str, Any]] = []
        self.tool_call_count: int = 0

    async def text_complete(  # type: ignore[override]
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        self.text_calls.append({
            "prompt": prompt, "system": system, "temperature": temperature,
        })
        return self.next_text

    async def tool_call(  # type: ignore[override]
        self,
        prompt: str,
        tool_schema: dict[str, Any],
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        self.tool_call_count += 1
        return {}


@pytest.fixture
def graph() -> SchemaGraph:
    return load(REAL_SCHEMA)


@pytest.fixture
def two_candidates(graph: SchemaGraph) -> list[list[Relation]]:
    """The canonical multi-path case: sys_user -> incident has two
    1-hop shortest chains (incidentsReported, incidentsAssigned)."""
    paths = graph.shortest_relation_paths("sys_user", "incident")
    assert len(paths) == 2, f"expected 2 candidates, got {len(paths)}"
    return paths


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_scorer_picks_caller_for_raised_phrasing(
    two_candidates: list[list[Relation]],
) -> None:
    llm = _ScriptedLLM()
    # Find the caller chain's index — depends on schema insertion order.
    caller_idx = next(
        i for i, chain in enumerate(two_candidates)
        if chain[0].id == "sys_user.incidentsReported"
    )
    llm.next_text = (
        f'{{"choice": {caller_idx}, "confidence": 0.92, '
        f'"reasoning": "user said \'raised\', matches caller side"}}'
    )
    scorer = RelationScorer(llm)
    choice = await scorer.choose(
        two_candidates,
        user_query="incidents Ravi raised",
        source_entity="sys_user",
        target_entity="incident",
    )
    assert isinstance(choice, ScorerChoice)
    assert choice.chosen_index == caller_idx
    assert choice.confidence == pytest.approx(0.92)
    assert "raised" in choice.reasoning.lower()


async def test_scorer_uses_only_text_complete_not_tool_call(
    two_candidates: list[list[Relation]],
) -> None:
    """Quarantined-role guarantee: the scorer must never invoke tools.
    DualLLMBoundary keeps the planner client distinct; the scorer
    shares the responder client which has no tool surface."""
    llm = _ScriptedLLM()
    await RelationScorer(llm).choose(
        two_candidates, "anything", "sys_user", "incident",
    )
    assert llm.tool_call_count == 0
    assert len(llm.text_calls) == 1


# ---------------------------------------------------------------------------
# Record-data isolation (Dual-LLM safety)
# ---------------------------------------------------------------------------


async def test_scorer_prompt_does_not_contain_record_data(
    two_candidates: list[list[Relation]],
) -> None:
    """The scorer prompt must contain ONLY schema-level metadata: the
    verbs, entity names, and the user's query string. It must never
    receive record data — assertion verified by length-bound + content
    grep."""
    llm = _ScriptedLLM()
    await RelationScorer(llm).choose(
        two_candidates,
        user_query="What's the status of John Doe's VPN issue?",
        source_entity="sys_user",
        target_entity="incident",
    )
    prompt = llm.text_calls[0]["prompt"]
    # Must contain the schema metadata.
    assert "sys_user" in prompt
    assert "incident" in prompt
    # Must contain a candidate description with the schema verb phrase.
    assert "raised incidents" in prompt or "is assigned" in prompt
    # Must NOT contain anything resembling a sys_id (a non-input value
    # that could only come from record data).
    assert "usr001" not in prompt
    assert "a1b2c3d4" not in prompt
    # Bounded in size — no record stream of any length.
    assert len(prompt) < 2000, f"prompt is suspiciously large: {len(prompt)} chars"


async def test_scorer_system_prompt_forbids_record_data() -> None:
    """The system prompt explicitly tells the LLM it sees only schema-level
    metadata. Asserting the documented intent."""
    llm = _ScriptedLLM()
    graph = load(REAL_SCHEMA)
    paths = graph.shortest_relation_paths("sys_user", "incident")
    await RelationScorer(llm).choose(
        paths, "x", "sys_user", "incident",
    )
    sysprompt = llm.text_calls[0]["system"]
    assert sysprompt is not None
    assert "schema" in sysprompt.lower()
    assert "record data" in sysprompt.lower()


# ---------------------------------------------------------------------------
# Fallback behaviour (no silent crashes)
# ---------------------------------------------------------------------------


async def test_scorer_falls_back_to_candidate_zero_on_invalid_json(
    two_candidates: list[list[Relation]],
) -> None:
    llm = _ScriptedLLM()
    llm.next_text = "not even json"
    choice = await RelationScorer(llm).choose(
        two_candidates, "anything", "sys_user", "incident",
    )
    assert choice.chosen_index == 0
    assert choice.confidence == 0.0
    assert "invalid json" in choice.reasoning.lower()


async def test_scorer_falls_back_on_out_of_range_choice(
    two_candidates: list[list[Relation]],
) -> None:
    llm = _ScriptedLLM()
    llm.next_text = '{"choice": 99, "confidence": 1.0, "reasoning": "x"}'
    choice = await RelationScorer(llm).choose(
        two_candidates, "anything", "sys_user", "incident",
    )
    assert choice.chosen_index == 0
    assert choice.confidence == 0.0


async def test_scorer_falls_back_on_non_object_response(
    two_candidates: list[list[Relation]],
) -> None:
    llm = _ScriptedLLM()
    llm.next_text = '[1, 2, 3]'
    choice = await RelationScorer(llm).choose(
        two_candidates, "anything", "sys_user", "incident",
    )
    assert choice.chosen_index == 0
    assert choice.confidence == 0.0


async def test_scorer_handles_markdown_fenced_json(
    two_candidates: list[list[Relation]],
) -> None:
    """Some providers wrap JSON in ```json ... ``` fences. The parser
    strips them before JSON-decoding."""
    llm = _ScriptedLLM()
    llm.next_text = (
        "```json\n"
        '{"choice": 1, "confidence": 0.7, "reasoning": "fenced ok"}\n'
        "```"
    )
    choice = await RelationScorer(llm).choose(
        two_candidates, "anything", "sys_user", "incident",
    )
    assert choice.chosen_index == 1
    assert choice.confidence == pytest.approx(0.7)


async def test_scorer_falls_back_on_llm_exception(
    two_candidates: list[list[Relation]],
) -> None:
    class _BoomLLM(_ScriptedLLM):
        async def text_complete(  # type: ignore[override]
            self,
            prompt: str,
            system: str | None = None,
            temperature: float = 0.0,
            max_tokens: int = 2048,
        ) -> str:
            raise RuntimeError("network down")

    choice = await RelationScorer(_BoomLLM()).choose(
        two_candidates, "anything", "sys_user", "incident",
    )
    assert choice.chosen_index == 0
    assert choice.confidence == 0.0
    assert "fail" in choice.reasoning.lower() or "fell back" in choice.reasoning.lower()


async def test_scorer_clamps_confidence_to_unit_interval(
    two_candidates: list[list[Relation]],
) -> None:
    llm = _ScriptedLLM()
    llm.next_text = '{"choice": 0, "confidence": 5.0, "reasoning": "x"}'
    choice = await RelationScorer(llm).choose(
        two_candidates, "anything", "sys_user", "incident",
    )
    assert choice.confidence == pytest.approx(1.0)

    llm.next_text = '{"choice": 0, "confidence": -2.0, "reasoning": "x"}'
    choice = await RelationScorer(llm).choose(
        two_candidates, "anything", "sys_user", "incident",
    )
    assert choice.confidence == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Trace data for the inspector
# ---------------------------------------------------------------------------


async def test_scorer_choice_carries_full_candidate_list(
    two_candidates: list[list[Relation]],
) -> None:
    """The trace step uses ScorerChoice.candidates to render the
    alternatives the LLM considered."""
    llm = _ScriptedLLM()
    choice = await RelationScorer(llm).choose(
        two_candidates, "x", "sys_user", "incident",
    )
    assert len(choice.candidates) == len(two_candidates)
    for i, c in enumerate(choice.candidates):
        assert c["index"] == i
        assert "path" in c
        assert "verb_chain" in c
        assert " --" in c["verb_chain"], (
            "verb_chain should render as 'entity --verb--> entity'"
        )


# ---------------------------------------------------------------------------
# Empty / degenerate inputs
# ---------------------------------------------------------------------------


async def test_scorer_raises_on_empty_path_list() -> None:
    """Calling the scorer with zero candidates is a programmer error
    (the walker should never do this)."""
    with pytest.raises(ValueError):
        await RelationScorer(_ScriptedLLM()).choose(
            [], "x", "sys_user", "incident",
        )
