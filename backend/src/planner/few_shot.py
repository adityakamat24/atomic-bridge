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
    """Embeds the example library and returns the top-k most similar
    examples to a query at planner-call time.

    **Lazy embedding**: the example library is NOT embedded in __init__.
    The first `select()` call triggers it. Keeps backend startup fast on
    cold-boot platforms (Fly.io etc.).
    """

    def __init__(
        self,
        examples: list[FewShotExample],
        embedding_client: EmbeddingClient,
    ) -> None:
        self._examples = list(examples)
        self._client = embedding_client
        self._embeddings: np.ndarray | None = None

    def _ensure_embedded(self) -> None:
        if self._embeddings is not None:
            return
        if self._examples:
            self._embeddings = self._client.embed(
                [e.query for e in self._examples]
            )
        else:
            self._embeddings = np.zeros(
                (0, self._client.dimension), dtype=np.float32
            )

    def select(self, query: str, top_k: int = 3) -> list[FewShotExample]:
        if not self._examples:
            return []
        self._ensure_embedded()
        assert self._embeddings is not None
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
