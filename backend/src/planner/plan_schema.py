from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.data_store import Filter

# ---------------------------------------------------------------------------
# Intent classification — also used by the preprocessor to short-circuit
# ambiguous / out_of_scope inputs without invoking the executor.
# ---------------------------------------------------------------------------

Intent = Literal[
    "lookup",
    "knowledge",
    "analytical",
    "cross_reference",
    "write_proposal",
    "ambiguous",
    "out_of_scope",
]


# ---------------------------------------------------------------------------
# The six operation types. Discriminated union via the `op` literal.
# Pydantic rejects unknown `op` values at parse time — that's the action
# selector defense (05-guardrails.md §Defense 1).
# ---------------------------------------------------------------------------


class FindOp(BaseModel):
    op: Literal["find"] = "find"
    id: str
    entity: str
    filters: list[Filter] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000)


class TraverseOp(BaseModel):
    """`from` is a Python keyword, so we accept it via alias and store as `from_var`."""

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["traverse"] = "traverse"
    id: str
    from_var: str = Field(alias="from")
    relation: str
    filters: list[Filter] = Field(default_factory=list)


class AggregateOp(BaseModel):
    op: Literal["aggregate"] = "aggregate"
    id: str
    source: str
    operation: Literal["count", "count_distinct", "group_by_count", "top_n"]
    group_by_field: str | None = None
    n: int | None = None


class KBLookupOp(BaseModel):
    op: Literal["kb_lookup"] = "kb_lookup"
    id: str
    query: str
    category_hint: str | None = None
    top_k: int = Field(default=3, ge=1, le=10)


class ResolveOp(BaseModel):
    op: Literal["resolve"] = "resolve"
    id: str
    source: str
    fields: list[str]
    include_relations: list[str] = Field(default_factory=list)
    apply_value_maps: bool = True


class WriteProposalOp(BaseModel):
    op: Literal["write_proposal"] = "write_proposal"
    id: str
    action: Literal["create_incident", "update_incident"]
    target_var: str | None = None
    fields: dict[str, str | int | bool] = Field(default_factory=dict)


Operation = Annotated[
    FindOp | TraverseOp | AggregateOp | KBLookupOp | ResolveOp | WriteProposalOp,
    Field(discriminator="op"),
]


# ---------------------------------------------------------------------------
# OutputSpec + QueryPlan
# ---------------------------------------------------------------------------


class OutputSpec(BaseModel):
    format: Literal[
        "single_item", "list", "scalar", "kb_answer", "write_confirmation"
    ]
    final_var: str
    max_items_shown: int = Field(default=20, ge=1, le=100)


class QueryPlan(BaseModel):
    intent: Intent
    reasoning: str = Field(..., min_length=10, max_length=2000)
    operations: list[Operation] = Field(default_factory=list)
    output_spec: OutputSpec | None = None
    confidence: float = Field(..., ge=0, le=1)
    clarification_needed: str | None = None

    @model_validator(mode="after")
    def validate_terminal_states(self) -> QueryPlan:
        if self.intent in ("ambiguous", "out_of_scope"):
            if self.operations:
                raise ValueError(
                    f"intent={self.intent!r} plans must have no operations"
                )
            if self.intent == "ambiguous" and not self.clarification_needed:
                raise ValueError(
                    "ambiguous plans must include a clarification_needed string"
                )
        else:
            if not self.operations:
                raise ValueError(
                    f"intent={self.intent!r} plans must include operations"
                )
            if not self.output_spec:
                raise ValueError(
                    f"intent={self.intent!r} plans must include output_spec"
                )
        return self


# ---------------------------------------------------------------------------
# Tool-schema helper for the LLM clients.
# ---------------------------------------------------------------------------


def query_plan_tool_schema() -> dict[str, Any]:
    """JSON Schema for the `generate_plan` tool. Both Anthropic and OpenAI
    accept the same shape via the LLM client abstraction.
    """
    return {
        "name": "generate_plan",
        "description": (
            "Emit a structured query plan for the IT service management "
            "mediation layer."
        ),
        "input_schema": QueryPlan.model_json_schema(),
    }
