from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.executor.engine import ExecutionResult
from src.llm.base import BaseLLMClient
from src.write_path.proposal import WriteProposal

DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "planner" / "prompts" / "response.md"
)


class ResponseGenerator:
    """The quarantined LLM (05-guardrails.md §Defense 3).

    It only ever sees:
    - The original user question (repeated before AND after the data — the
      Sandwich Defense from Liu et al. 2024).
    - The execution result wrapped in <data_source>...</data_source> tags
      (the Spotlighting pattern).

    It cannot invoke tools. Its output is plain text. The caller (`api/routes`)
    runs the output filter (Phase 9) before returning to the client.
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        prompt_path: Path = DEFAULT_PROMPT_PATH,
    ) -> None:
        self._llm = llm
        self._template = prompt_path.read_text(encoding="utf-8")

    async def generate(
        self, question: str, result: ExecutionResult, max_tokens: int = 1024
    ) -> str:
        body = _serialise_for_prompt(result)
        # The full template IS the message — there's no separate "user prompt"
        # because we want the question wrapped inside the spotlighting block.
        # text_complete takes both system and prompt; we put the framing in
        # the system slot so the user message stays minimal & predictable.
        prompt = self._template.format(
            question=question,
            execution_result_json=body,
        )
        # System message is intentionally empty — all the framing is in the
        # prompt body so the boundary between instructions and data is
        # explicit and obvious to the model.
        return await self._llm.text_complete(
            prompt=prompt,
            system=None,
            temperature=0.0,
            max_tokens=max_tokens,
        )


def _serialise_for_prompt(result: ExecutionResult) -> str:
    """Render the execution result as compact JSON.

    Includes the planner's reasoning + an op summary so the response
    generator understands what filters were already applied. Without this
    context, the LLM frequently sees a list of records, doesn't realise
    the planner already filtered correctly, and refuses with "I don't
    have data on that" — the exact answer the user is looking for.
    """
    plan = result.plan
    op_summary = []
    for op in plan.operations:
        d = op.model_dump(by_alias=True, exclude_none=True)
        op_summary.append(d)

    payload: dict[str, Any] = {
        "intent": plan.intent,
        "planner_reasoning": plan.reasoning,
        "operations_executed": op_summary,
        "format": (plan.output_spec.format if plan.output_spec else None),
        "warnings": result.warnings,
    }
    output = result.output
    if isinstance(output, WriteProposal):
        payload["write_proposal"] = output.model_dump(mode="json")
    elif output is None:
        payload["data"] = None
    else:
        payload["data"] = output
    return json.dumps(payload, indent=2, default=str)
