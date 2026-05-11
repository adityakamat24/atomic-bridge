from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.schema_graph import SchemaGraph
from src.llm.base import BaseLLMClient
from src.planner.few_shot import FewShotRetriever
from src.planner.plan_schema import QueryPlan, query_plan_tool_schema
from src.planner.preprocessor import EntityMention

PROMPT_PATH = Path(__file__).parent / "prompts" / "plan_generator.md"


class PlanGenerator:
    """Agent 2 of the planner pipeline.

    Takes the preprocessed query + entity mentions + relevant subgraph IDs
    and asks the LLM to emit a `QueryPlan` via the `generate_plan` tool.
    Few-shot retrieval narrows the prompt to the most similar examples.

    The result is parsed by Pydantic (`QueryPlan.model_validate`). Pydantic
    rejection is propagated up — the planner does NOT retry on bad output
    (see 03-planner.md and 05-guardrails.md).
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        graph: SchemaGraph,
        few_shot: FewShotRetriever,
        prompt_path: Path = PROMPT_PATH,
        few_shot_k: int = 3,
        subgraph_hops: int = 2,
    ) -> None:
        self._llm = llm
        self._graph = graph
        self._few_shot = few_shot
        self._template = prompt_path.read_text(encoding="utf-8")
        self._few_shot_k = few_shot_k
        self._subgraph_hops = subgraph_hops

    async def generate(
        self,
        question: str,
        rewritten_query: str,
        resolved_entities: list[EntityMention],
        subgraph_ids: list[str] | None = None,
    ) -> QueryPlan:
        # Transitively expand the preprocessor's subgraph hint along the
        # relation graph so the planner always sees entities reachable through
        # a traversal, even when the preprocessor didn't name them explicitly.
        # See 03-planner.md §subgraph-filtering.
        expanded: list[str] | None = None
        if subgraph_ids:
            expanded = self._graph.expand_subgraph(
                subgraph_ids, max_hops=self._subgraph_hops
            )
        examples = self._few_shot.select(rewritten_query, top_k=self._few_shot_k)
        system = self._template.format(
            schema_subgraph=self._graph.to_llm_context(expanded),
            resolved_entities=_format_entities(resolved_entities),
            question=question,
            rewritten=rewritten_query,
            few_shot_examples=self._few_shot.format_for_prompt(examples),
        )
        raw = await self._llm.tool_call(
            prompt=rewritten_query,
            tool_schema=query_plan_tool_schema(),
            system=system,
            temperature=0.0,
            max_tokens=4096,
        )
        # Pydantic validation: bad ops, missing fields etc. raise here.
        return QueryPlan.model_validate(_normalise_plan(raw))


def _format_entities(items: list[EntityMention]) -> str:
    if not items:
        return "(none — the question has no resolved entity mentions)"
    return "\n".join(
        f"- {m.surface!r}: {m.entity_type} -> sys_id={m.resolved_sys_id} "
        f"(candidates: {m.candidates})"
        for m in items
    )


def _normalise_plan(raw: dict[str, Any]) -> dict[str, Any]:
    """LLMs occasionally return `from_var` instead of `from` for traverse ops
    (despite the alias). Patch it back so Pydantic accepts."""
    ops = raw.get("operations", [])
    for op in ops:
        if op.get("op") == "traverse" and "from" not in op and "from_var" in op:
            op["from"] = op.pop("from_var")
    return raw
