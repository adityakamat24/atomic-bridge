"""LLM-driven relation-scoring pass.

Invoked by SchemaGraph.walk when the planner emits a declarative
traverse with `to_entity` but no `path`, and more than one simple chain
exists between source and target. The scorer ranks the candidates by
which one best matches the user's phrasing, replacing what would
otherwise be hard-coded role-routing rules.

The scorer is a separate LLM role that shares the quarantined
responder client (text-only, no tool use). It sees schema metadata and
the user query, never record data. DualLLMBoundary asserts the planner
client and the quarantined client are distinct objects at startup.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from src.core.schema_graph import Relation
from src.llm.base import BaseLLMClient

_SYSTEM_PROMPT = """You are the relation-scoring agent for an IT service management mediation layer.

You operate on schema-level metadata ONLY. You never see record data — no sys_ids, no field values, no user lists. Your inputs come from the schema graph (entity names and relation verb_phrases) plus the user's natural-language query.

The planner has emitted a declarative traverse op of the form "from $x reach <to_entity>". Multiple candidate relation chains exist between the source entity and the target. Your job is to RANK them — most-likely-intended-by-the-user first.

You will see:
1. The user's original natural-language query.
2. The source entity and the target entity (entity ids from the schema).
3. A numbered list of candidate chains, each rendered as `entity --verb_phrase--> entity --verb_phrase--> ...`. The verb_phrase values are pulled directly from the schema. Chains are presented shortest-first; longer chains DO exist and are valid — pick by phrasing fit, not by length.

Examples of verb-to-phrasing mapping (these describe what specific schema verbs mean — they are NOT routing rules, just documentation of the verb_phrase strings you'll see in the candidates):

- "raised", "reported", "submitted", "opened" → caller-side verb_phrases such as `raised incidents`, `reported by`
- "assigned", "working on", "handling", "is on" → assignee or team-side verb_phrases such as `is assigned`, `assigned to`, `handled by team`, `handles incidents`
- "manages", "reports to", "team manager" → manager-side verb_phrases such as `manages`, `managed by`, `manages team`
- "in", "about", "for" + category → category-side verb_phrases such as `in category`, `has articles`, `has incidents`

When the user's wording is ambiguous (e.g. "their team" without a verb that singles out caller vs. assignee vs. manager), list all the plausible chains in your ranking — the executor walks the ranking and falls back to the next chain when one returns zero records, so you don't have to guess; just rank by phrasing fit and let the data decide.

Return a JSON object with a RANKED list of indices. The executor walks them in order, falling back to the next when a chain returns zero records.

OUTPUT FORMAT (strict JSON, no surrounding text):

{
  "ranking": [<int index>, <int index>, ...],
  "confidence": <float 0.0 to 1.0>,
  "reasoning": "<short sentence, max 200 chars, explaining the top pick>"
}

The `ranking` array MUST contain every candidate index (0..N-1) exactly once, in your preferred order — most-likely first.

Do not include any explanation outside the JSON. Do not include record data."""


@dataclass(frozen=True)
class ScorerChoice:
    """Ranking + data-adaptive fallback in place of hardcoded role rules.

    The engine walks the top-ranked chain first; if it returns zero
    records, it falls back to the next-ranked.
    """

    ranking: list[int]
    confidence: float
    reasoning: str
    latency_ms: int
    candidates: list[dict[str, Any]]  # for trace inspection

    @property
    def chosen_index(self) -> int:
        return self.ranking[0] if self.ranking else 0


class RelationScorer:
    """LLM-driven path picker. Invoked by SchemaGraph.walk only when
    the planner left `path` unspecified AND multiple shortest chains
    exist between source and target entity."""

    def __init__(self, llm: BaseLLMClient) -> None:
        self._llm = llm

    async def choose(
        self,
        paths: list[list[Relation]],
        user_query: str,
        source_entity: str,
        target_entity: str,
    ) -> ScorerChoice:
        if not paths:
            raise ValueError("scorer called with empty paths list")
        # Build the candidate descriptions. The scorer sees only the
        # schema-level verbs — never record data.
        candidates_for_trace: list[dict[str, Any]] = []
        candidate_lines: list[str] = []
        for i, chain in enumerate(paths):
            verbs = self._render_chain(chain, source_entity)
            candidates_for_trace.append({
                "index": i,
                "path": [rel.id for rel in chain],
                "verb_chain": verbs,
            })
            candidate_lines.append(f"  {i}. {verbs}")

        prompt = (
            f'User query: "{user_query}"\n\n'
            f"Source entity: {source_entity}\n"
            f"Target entity: {target_entity}\n\n"
            f"Candidate shortest chains ({len(paths)}):\n"
            + "\n".join(candidate_lines)
            + "\n\nReturn the JSON object now."
        )

        t0 = time.perf_counter()
        try:
            raw = await self._llm.text_complete(
                prompt=prompt,
                system=_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=512,
            )
        except Exception:  # noqa: BLE001
            # Hard-fall back to insertion-order (shortest-first) on any
            # LLM failure. The trace shows confidence=0 so callers can flag.
            elapsed = int((time.perf_counter() - t0) * 1000)
            return ScorerChoice(
                ranking=list(range(len(paths))),
                confidence=0.0,
                reasoning="scorer LLM failed; using shortest-first fallback",
                latency_ms=elapsed,
                candidates=candidates_for_trace,
            )
        elapsed = int((time.perf_counter() - t0) * 1000)

        return self._parse_or_fallback(raw, len(paths), candidates_for_trace, elapsed)

    @staticmethod
    def _render_chain(chain: list[Relation], source_entity: str) -> str:
        """Render a relation chain as `entity --verb--> entity --verb--> entity`.
        Only used in the scorer prompt — purely schema metadata."""
        if not chain:
            return source_entity
        parts: list[str] = [source_entity]
        for rel in chain:
            parts.append(f"--{rel.verb_phrase}-->")
            parts.append(rel.to_entity)
        return " ".join(parts)

    @staticmethod
    def _parse_or_fallback(
        raw: str,
        n_candidates: int,
        candidates_for_trace: list[dict[str, Any]],
        latency_ms: int,
    ) -> ScorerChoice:
        """Best-effort JSON parse. Falls back to insertion-order ranking
        with confidence=0 on any malformation. Also accepts the old
        single-``choice`` shape (with confidence=1.0 the chosen index
        first, then the rest insertion-order) so any provider that
        produced the old format still works."""
        text = raw.strip()
        # Strip markdown fences if present.
        if text.startswith("```"):
            lines = text.splitlines()
            if lines:
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return ScorerChoice(
                ranking=list(range(n_candidates)),
                confidence=0.0,
                reasoning="scorer returned invalid JSON; using shortest-first fallback",
                latency_ms=latency_ms,
                candidates=candidates_for_trace,
            )
        if not isinstance(obj, dict):
            return ScorerChoice(
                ranking=list(range(n_candidates)),
                confidence=0.0,
                reasoning="scorer returned non-object; using shortest-first fallback",
                latency_ms=latency_ms,
                candidates=candidates_for_trace,
            )

        reasoning = str(obj.get("reasoning", ""))[:200] or "(no reasoning provided)"
        try:
            confidence = float(obj.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        ranking_raw = obj.get("ranking")
        if isinstance(ranking_raw, list) and ranking_raw:
            # Dedupe + filter to valid indices, preserving order.
            seen: set[int] = set()
            ranking: list[int] = []
            for x in ranking_raw:
                if isinstance(x, int) and 0 <= x < n_candidates and x not in seen:
                    seen.add(x)
                    ranking.append(x)
            # Append any candidates the scorer omitted, insertion-order,
            # so the engine has a complete fallback list.
            for i in range(n_candidates):
                if i not in seen:
                    ranking.append(i)
            if ranking:
                return ScorerChoice(
                    ranking=ranking,
                    confidence=confidence,
                    reasoning=reasoning,
                    latency_ms=latency_ms,
                    candidates=candidates_for_trace,
                )

        # Back-compat: accept the older single ``choice`` field.
        choice = obj.get("choice")
        if isinstance(choice, int) and 0 <= choice < n_candidates:
            ranking = [choice] + [i for i in range(n_candidates) if i != choice]
            return ScorerChoice(
                ranking=ranking,
                confidence=confidence,
                reasoning=reasoning,
                latency_ms=latency_ms,
                candidates=candidates_for_trace,
            )

        # Nothing usable. Fall back to insertion-order.
        return ScorerChoice(
            ranking=list(range(n_candidates)),
            confidence=0.0,
            reasoning="scorer returned no usable ranking; using shortest-first fallback",
            latency_ms=latency_ms,
            candidates=candidates_for_trace,
        )
