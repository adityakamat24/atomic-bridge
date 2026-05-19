"""Single-LLM-call planner.

Replaces the old preprocessor + plan_generator + few-shot retriever
pipeline. The planner emits a fully-formed QueryPlan from a tight system
prompt + the schema markdown + the user's query (and an optional prior
turn for pronoun resolution).

Why one LLM call instead of three:
  * The preprocessor's only LLM-side responsibility was intent
    classification and entity extraction. Modern Claude / GPT do both
    inline as part of plan emission when the system prompt is explicit
    about the intent set.
  * Name resolution is deterministic and lives in the data store
    (planner emits `find sys_user filters=[name contains "..."]`); no
    application-layer pre-indexed fuzzy match.
  * Few-shot examples were a crutch for the role-routing rules that no
    longer exist. The declarative `traverse to_entity=X path=[...]`
    shape combined with the JSON schema is enough for the LLM to follow.

The single LLM call still gets tool-use enforcement (Anthropic
`tool_choice` / OpenAI structured output force the QueryPlan shape), and
the Pydantic validator hard-rejects any plan whose names aren't in the
schema or whose chain isn't a valid shortest path.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.core.schema_graph import SchemaGraph
from src.llm.base import BaseLLMClient
from src.planner.plan_schema import QueryPlan, query_plan_tool_schema
from src.planner.plan_validator import PlanValidator

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "planner.md"


@dataclass(frozen=True)
class PriorTurn:
    """A snapshot of the previous conversation turn the planner uses
    to resolve pronouns. The session store builds this from the previous
    response's plan."""

    query: str
    resolved_entities: tuple[str, ...] = ()


class Planner:
    """One privileged LLM call -> a validated QueryPlan.

    The privileged client has tool use; the response generator (separate
    quarantined client) does not. DualLLMBoundary asserts they are
    distinct at app startup.
    """

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
    ) -> QueryPlan:
        prompt = self._build_prompt(query, prior_turn)
        raw = await self._llm.tool_call(
            prompt,
            query_plan_tool_schema(),
            system=self._system_template,
            temperature=0.0,
            max_tokens=4096,
        )
        plan = QueryPlan.model_validate(raw)
        # Pure-Python validator: rejects invented names, non-shortest
        # paths, off-chain filters, exceeded resource caps, etc. Also
        # translates display-string filter values to value-map codes.
        return self._validator.validate(plan)

    def _build_prompt(
        self,
        query: str,
        prior_turn: PriorTurn | None,
    ) -> str:
        schema_md = self._graph.to_llm_context()
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
            f"{prior_block}"
            f"## User query\n\n{query}\n\n"
            f"Emit a QueryPlan via the generate_plan tool."
        )
