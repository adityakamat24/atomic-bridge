from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.knowledge.embeddings import EmbeddingClient
from src.planner.few_shot import FewShotExample, FewShotRetriever, load_examples

EXAMPLES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "planner"
    / "prompts"
    / "few_shot.yaml"
)


class _DeterministicClient:
    """8-dim keyword embedder, identical idea to test_kb_retriever."""

    KEYWORDS = (
        "vpn",
        "outlook",
        "engineering",
        "desktop",
        "create",
        "close",
        "all",
        "ignore",
    )

    @property
    def dimension(self) -> int:
        return len(self.KEYWORDS)

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, t in enumerate(texts):
            tl = t.lower()
            for j, kw in enumerate(self.KEYWORDS):
                if kw in tl:
                    out[i, j] = 1.0
            n = float(np.linalg.norm(out[i]))
            if n > 0:
                out[i] /= n
        return out


@pytest.fixture
def embedder() -> EmbeddingClient:
    return _DeterministicClient()


@pytest.fixture
def examples() -> list[FewShotExample]:
    return load_examples(EXAMPLES_PATH)


def test_load_examples_yields_full_set(examples: list[FewShotExample]) -> None:
    """Library is grown over time; assert well-known anchors are present."""
    assert len(examples) >= 7
    assert any("Outlook" in e.query for e in examples)
    assert any("Ravi Kumar" in e.query for e in examples)


def test_load_examples_each_has_plan_dict(examples: list[FewShotExample]) -> None:
    for e in examples:
        assert isinstance(e.plan, dict)
        assert "intent" in e.plan
        assert "operations" in e.plan


def test_few_shot_retriever_picks_outlook_example_for_outlook_query(
    examples: list[FewShotExample], embedder: EmbeddingClient
) -> None:
    r = FewShotRetriever(examples, embedder)
    selected = r.select("Outlook keeps crashing", top_k=1)
    assert "Outlook" in selected[0].query


def test_few_shot_retriever_picks_close_example_for_close_query(
    examples: list[FewShotExample], embedder: EmbeddingClient
) -> None:
    r = FewShotRetriever(examples, embedder)
    selected = r.select("close incident INC0099999", top_k=1)
    assert "Close INC" in selected[0].query


def test_few_shot_top_k_bounds_results(
    examples: list[FewShotExample], embedder: EmbeddingClient
) -> None:
    r = FewShotRetriever(examples, embedder)
    assert len(r.select("anything", top_k=3)) <= 3


def test_few_shot_format_for_prompt_includes_query_and_plan(
    examples: list[FewShotExample], embedder: EmbeddingClient
) -> None:
    r = FewShotRetriever(examples, embedder)
    text = r.format_for_prompt(r.select("vpn", top_k=1))
    assert "Query" in text
    assert "Plan (JSON)" in text


def test_empty_examples_yields_empty_selection(
    embedder: EmbeddingClient,
) -> None:
    r = FewShotRetriever([], embedder)
    assert r.select("anything") == []
