"""Single-LLM-call planner: schema + view-scope + query -> validated
QueryPlan via tool_call. Pure-Python validator rejects invented names,
non-shortest paths, and role-scope violations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.core.schema_graph import SchemaGraph
from src.guardrails.view_scope import ViewScope
from src.llm.base import BaseLLMClient
from src.planner.plan_schema import QueryPlan, query_plan_tool_schema
from src.planner.plan_validator import PlanValidator

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "planner.md"


@dataclass(frozen=True)
class PriorTurn:
    """Previous turn's query + resolved entity ids, used for pronoun
    resolution."""

    query: str
    resolved_entities: tuple[str, ...] = ()


class Planner:
    def __init__(
        self,
        llm: BaseLLMClient,
        graph: SchemaGraph,
        *,
        prompt_path: Path = PROMPT_PATH,
        allow_pii: bool = False,
    ) -> None:
        self._llm = llm
        self._graph = graph
        self._system_template = prompt_path.read_text(encoding="utf-8")
        self._validator = PlanValidator(graph, allow_pii=allow_pii)

    async def plan(
        self,
        query: str,
        prior_turn: PriorTurn | None = None,
        view_scope: ViewScope | None = None,
    ) -> QueryPlan:
        prompt = self._build_prompt(query, prior_turn, view_scope)
        raw = await self._llm.tool_call(
            prompt,
            query_plan_tool_schema(),
            system=self._system_template,
            temperature=0.0,
            max_tokens=4096,
        )
        plan = QueryPlan.model_validate(raw)
        return self._validator.validate(plan, view_scope=view_scope)

    def _build_prompt(
        self,
        query: str,
        prior_turn: PriorTurn | None,
        view_scope: ViewScope | None,
    ) -> str:
        schema_md = self._graph.to_llm_context()
        scope_block = ""
        if view_scope is not None and not view_scope.is_admin:
            scope_block = view_scope.context_for_planner_prompt() + "\n"
        prior_block = ""
        if prior_turn:
            prior_block = (
                "## Prior turn (for pronoun / 'them' / 'that' resolution)\n\n"
                f"Previous user query: {prior_turn.query!r}\n"
            )
            if prior_turn.resolved_entities:
                prior_block += (
                    f"Previously-resolved entity ids: "
                    f"{list(prior_turn.resolved_entities)!r}\n"
                )
            prior_block += "\n"
        return (
            f"## Schema\n\n{schema_md}\n"
            f"{scope_block}"
            f"{prior_block}"
            f"## User query\n\n{query}\n\n"
            f"Emit a QueryPlan via the generate_plan tool."
        )
