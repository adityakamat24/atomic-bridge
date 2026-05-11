from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from src.knowledge.embeddings import EmbeddingClient


@dataclass
class FewShotExample:
    query: str
    intent_hint: str
    plan: dict[str, Any]


def load_examples(path: Path | str) -> list[FewShotExample]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [
        FewShotExample(
            query=e["query"],
            intent_hint=e.get("intent_hint", ""),
            plan=e["plan"],
        )
        for e in raw.get("examples", [])
    ]


class FewShotRetriever:
    """Embeds the example library once at startup and returns the top-k
    most similar examples to a query at planner-call time.
    """

    def __init__(
        self,
        examples: list[FewShotExample],
        embedding_client: EmbeddingClient,
    ) -> None:
        self._examples = list(examples)
        self._client = embedding_client
        if examples:
            self._embeddings = embedding_client.embed([e.query for e in examples])
        else:
            self._embeddings = np.zeros((0, embedding_client.dimension), dtype=np.float32)

    def select(self, query: str, top_k: int = 3) -> list[FewShotExample]:
        if not self._examples:
            return []
        q = self._client.embed([query])
        # Inner product on already-normalised vectors == cosine similarity.
        scores = (q @ self._embeddings.T)[0]
        order = np.argsort(-scores)[:top_k]
        return [self._examples[int(i)] for i in order]

    def format_for_prompt(self, examples: list[FewShotExample]) -> str:
        chunks: list[str] = []
        for e in examples:
            chunks.append(f"### Query\n{e.query}\n\n### Hint\n{e.intent_hint}\n")
            chunks.append("### Plan (JSON)\n```json\n")
            chunks.append(yaml.safe_dump(e.plan, sort_keys=False).rstrip())
            chunks.append("\n```\n")
        return "\n".join(chunks)
