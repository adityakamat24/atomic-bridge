from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.core.schema_graph import SchemaGraph
from src.llm.base import BaseLLMClient
from src.planner.name_resolver import NameResolver
from src.planner.plan_schema import Intent

PROMPT_PATH = Path(__file__).parent / "prompts" / "preprocessor.md"


class EntityMention(BaseModel):
    surface: str
    entity_type: str
    resolved_sys_id: str | None = None
    candidates: list[str] = Field(default_factory=list)


class PreprocessorOutput(BaseModel):
    intent: Intent
    rewritten_query: str
    entity_mentions: list[EntityMention] = Field(default_factory=list)
    relevant_entities: list[str] = Field(default_factory=list)
    notes: str = ""


@dataclass
class PriorTurn:
    """Snapshot of the previous turn used by the preprocessor for pronoun
    resolution. Phase 10 wires this from the session store."""

    query: str = ""
    resolved_entities: list[dict[str, Any]] = field(default_factory=list)


_TOOL_NAME = "preprocess"
_TOOL_SCHEMA = {
    "name": _TOOL_NAME,
    "description": (
        "Emit the preprocessed query: intent classification, entity mentions, "
        "relevant entities, and a rewrite of any pronouns."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": [
                    "lookup",
                    "knowledge",
                    "analytical",
                    "cross_reference",
                    "write_proposal",
                    "ambiguous",
                    "out_of_scope",
                ],
            },
            "rewritten_query": {"type": "string"},
            "entity_mentions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "surface": {"type": "string"},
                        "entity_type": {"type": "string"},
                        "resolved_sys_id": {"type": ["string", "null"]},
                        "candidates": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["surface", "entity_type"],
                },
            },
            "relevant_entities": {
                "type": "array",
                "items": {"type": "string"},
            },
            "notes": {"type": "string"},
        },
        "required": [
            "intent",
            "rewritten_query",
            "entity_mentions",
            "relevant_entities",
        ],
    },
}


class Preprocessor:
    """Agent 1 of the planner pipeline (03-planner.md).

    Calls the LLM once with the schema summary and the deterministic
    name-resolver candidates pre-attached, then validates the response
    into a `PreprocessorOutput`.
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        graph: SchemaGraph,
        name_resolver: NameResolver,
        prompt_path: Path = PROMPT_PATH,
    ) -> None:
        self._llm = llm
        self._graph = graph
        self._name_resolver = name_resolver
        self._template = prompt_path.read_text(encoding="utf-8")

    @staticmethod
    def tool_schema() -> dict[str, Any]:
        return _TOOL_SCHEMA

    async def process(
        self, query: str, prior: PriorTurn | None = None
    ) -> PreprocessorOutput:
        prior = prior or PriorTurn()
        candidates_text = self._format_candidates(query)
        system = self._template.format(
            schema_summary=self._graph.to_llm_context(),
            prior_query=prior.query or "(none)",
            prior_resolved_entities=_format_resolved(prior.resolved_entities),
            name_candidates=candidates_text,
        )
        raw = await self._llm.tool_call(
            prompt=query,
            tool_schema=_TOOL_SCHEMA,
            system=system,
            temperature=0.0,
        )
        return PreprocessorOutput.model_validate(raw)

    def _format_candidates(self, query: str) -> str:
        # Cheap heuristic: try every capitalised token & every two-token
        # sequence as a possible name. Quantity is small in our prototype.
        tokens = [t.strip(",.!?") for t in query.split()]
        candidates_for: dict[str, Any] = {}
        for t in tokens:
            if t and t[0].isupper():
                candidates_for[t] = self._name_resolver.resolve(t)
        for i in range(len(tokens) - 1):
            two = f"{tokens[i]} {tokens[i + 1]}"
            if two[0].isupper():
                candidates_for[two] = self._name_resolver.resolve(two)
        if not candidates_for:
            return "(none — no name-shaped tokens detected)"
        lines: list[str] = []
        for mention, cands in candidates_for.items():
            if not cands:
                continue
            cand_strs = ", ".join(
                f"{c.sys_id}={c.full_name}({c.match_type},{c.confidence:.2f})"
                for c in cands[:3]
            )
            lines.append(f"- {mention!r}: {cand_strs}")
        return "\n".join(lines) if lines else "(none — no name matches)"


def _format_resolved(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(none)"
    return ", ".join(
        f"{i.get('entity_type', '?')}:{i.get('sys_id', '?')}" for i in items
    )


# Public re-exports
__all__ = ["EntityMention", "PreprocessorOutput", "Preprocessor", "PriorTurn"]
_LITERAL_GUARD: Literal["preprocessor"] = "preprocessor"  # mypy anchor
