"""Single-LLM-call response generator.

Replaces the old ResponseGenerator. Same protocol (text-in/text-out,
quarantined LLM client with no tool use), updated for the new
ExecutionResult shape (lives in core/trace.py now that the executor
module is gone).

The responder doesn't pick paths — the planner already did that, and
the trace carries the chain. The responder synthesizes prose from the
records the executor returned, the planner's reasoning, and any
warnings (dangling references, empty results) the executor surfaced.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.trace import ExecutionResult
from src.llm.base import BaseLLMClient
from src.planner.plan_schema import QueryPlan
from src.write_path.proposal import WriteProposal

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "responder.md"


class Responder:
    """The quarantined LLM — sees data, cannot invoke tools.

    Defense layering:
      * Spotlighting (Liu et al., USENIX 2024): data is wrapped in
        <data_source>...</data_source> tags; the prompt explicitly
        tells the model that anything inside is content, not instructions.
      * Sandwich (same paper): the user's question is repeated before
        AND after the data block so the model re-attends to it after
        reading potentially-adversarial record text.
      * Dual LLM (Willison, 2023): planner LLM (privileged) never sees
        record data; this responder LLM (quarantined) never has tools.
        DualLLMBoundary.assert_distinct() at app startup guards the split.
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        *,
        prompt_path: Path = PROMPT_PATH,
    ) -> None:
        self._llm = llm
        self._template = prompt_path.read_text(encoding="utf-8")

    async def respond(
        self,
        query: str,
        plan: QueryPlan,
        result: ExecutionResult,
        *,
        max_tokens: int = 1024,
    ) -> str:
        body = _serialise(plan, result)
        prompt = self._template.format(
            question=query,
            execution_result_json=body,
        )
        # System message intentionally empty — the framing lives in the
        # prompt body so the data/instruction boundary is explicit.
        return await self._llm.text_complete(
            prompt=prompt,
            system=None,
            temperature=0.0,
            max_tokens=max_tokens,
        )


def _serialise(plan: QueryPlan, result: ExecutionResult) -> str:
    """Compact JSON the responder sees. Includes the planner's reasoning
    + operation summary so the LLM has context for empty results."""
    op_summary: list[dict[str, Any]] = []
    for op in plan.operations:
        op_summary.append(op.model_dump(by_alias=True, exclude_none=True))

    payload: dict[str, Any] = {
        "intent": plan.intent,
        "planner_reasoning": plan.reasoning,
        "operations_executed": op_summary,
        "format": plan.output_spec.format if plan.output_spec else None,
        "warnings": result.warnings,
    }
    # Trace chain so the responder can mention "via the team's open
    # incidents" — useful for transparency when multi-hop walks happen.
    chain = result.trace.to_visjs_highlights()
    if chain:
        payload["graph_chain_walked"] = chain

    output = result.output
    if isinstance(output, WriteProposal):
        payload["write_proposal"] = output.model_dump(mode="json")
    elif output is None:
        payload["data"] = None
    else:
        payload["data"] = output
    return json.dumps(payload, indent=2, default=str)
