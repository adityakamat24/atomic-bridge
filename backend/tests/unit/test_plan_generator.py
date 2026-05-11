from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest
from pydantic import ValidationError

from src.core.schema_loader import load
from src.planner.few_shot import FewShotRetriever, load_examples
from src.planner.plan_generator import PlanGenerator
from src.planner.plan_schema import FindOp, QueryPlan, ResolveOp, TraverseOp
from src.planner.preprocessor import EntityMention

REPO_DATA_SCHEMA = (
    Path(__file__).resolve().parents[2] / "data" / "schema.yaml"
)
EXAMPLES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "planner"
    / "prompts"
    / "few_shot.yaml"
)


class _StaticEmbedder:
    """All-zero embedder so few_shot.select() returns examples in insertion order."""

    @property
    def dimension(self) -> int:
        return 4

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), 4), dtype=np.float32)


@pytest.fixture
def generator() -> tuple[PlanGenerator, AsyncMock]:
    g = load(REPO_DATA_SCHEMA)
    examples = load_examples(EXAMPLES_PATH)
    fs = FewShotRetriever(examples, _StaticEmbedder())
    fake_llm = AsyncMock()
    return PlanGenerator(fake_llm, g, fs), fake_llm.tool_call


@pytest.mark.asyncio
async def test_lookup_plan_parses(
    generator: tuple[PlanGenerator, AsyncMock],
) -> None:
    gen, tool_call = generator
    tool_call.return_value = {
        "intent": "lookup",
        "reasoning": "Find John then his incidents",
        "operations": [
            {"op": "find", "id": "u", "entity": "sys_user"},
            {
                "op": "traverse",
                "id": "i",
                "from": "$u",
                "relation": "sys_user.incidentsReported",
            },
            {"op": "resolve", "id": "out", "source": "$i", "fields": ["number", "state"]},
        ],
        "output_spec": {"format": "list", "final_var": "out"},
        "confidence": 0.92,
    }
    plan = await gen.generate(
        "What's John's status?",
        "What's John's status?",
        [EntityMention(surface="John", entity_type="sys_user", resolved_sys_id="usr001")],
    )
    assert isinstance(plan, QueryPlan)
    assert isinstance(plan.operations[0], FindOp)
    assert isinstance(plan.operations[1], TraverseOp)
    assert isinstance(plan.operations[2], ResolveOp)


@pytest.mark.asyncio
async def test_normalises_from_var_back_to_from_alias(
    generator: tuple[PlanGenerator, AsyncMock],
) -> None:
    """LLMs sometimes emit `from_var` instead of `from`. The generator
    transparently fixes that."""
    gen, tool_call = generator
    tool_call.return_value = {
        "intent": "lookup",
        "reasoning": "x" * 12,
        "operations": [
            {"op": "find", "id": "u", "entity": "sys_user"},
            {
                "op": "traverse",
                "id": "i",
                "from_var": "$u",
                "relation": "sys_user.incidentsReported",
            },
            {"op": "resolve", "id": "out", "source": "$i", "fields": ["number"]},
        ],
        "output_spec": {"format": "list", "final_var": "out"},
        "confidence": 0.9,
    }
    plan = await gen.generate("x", "x", [])
    assert isinstance(plan.operations[1], TraverseOp)


@pytest.mark.asyncio
async def test_ambiguous_plan_parses_with_no_ops(
    generator: tuple[PlanGenerator, AsyncMock],
) -> None:
    gen, tool_call = generator
    tool_call.return_value = {
        "intent": "ambiguous",
        "reasoning": "too broad to action",
        "operations": [],
        "confidence": 0.4,
        "clarification_needed": "What entity?",
    }
    plan = await gen.generate("Show me all data", "Show me all data", [])
    assert plan.intent == "ambiguous"
    assert plan.clarification_needed == "What entity?"


@pytest.mark.asyncio
async def test_prompt_includes_schema_subgraph_and_examples(
    generator: tuple[PlanGenerator, AsyncMock],
) -> None:
    gen, tool_call = generator
    tool_call.return_value = {
        "intent": "lookup",
        "reasoning": "x" * 12,
        "operations": [{"op": "find", "id": "x", "entity": "incident"}],
        "output_spec": {"format": "list", "final_var": "x"},
        "confidence": 0.9,
    }
    await gen.generate("vpn", "vpn", [], subgraph_ids=["incident"])
    system = tool_call.call_args.kwargs["system"]
    assert "## incident" in system
    assert "Plan (JSON)" in system  # few-shot block present
    # Subgraph filter respected
    assert "## sys_user_group" not in system


@pytest.mark.asyncio
async def test_invalid_plan_response_raises_validation_error(
    generator: tuple[PlanGenerator, AsyncMock],
) -> None:
    gen, tool_call = generator
    tool_call.return_value = {
        "intent": "lookup",
        "reasoning": "missing operations",
        "operations": [],  # invalid for lookup
        "confidence": 0.9,
    }
    with pytest.raises(ValidationError):
        await gen.generate("x", "x", [])
