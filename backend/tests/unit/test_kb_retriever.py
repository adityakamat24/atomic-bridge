from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from src.core.in_memory_store import InMemoryStore
from src.core.schema_loader import load
from src.knowledge.embeddings import EmbeddingClient
from src.knowledge.indexer import KBIndexer, article_text
from src.knowledge.retriever import KBRetriever

REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


# ---------- Deterministic test embedder -------------------------------------


class KeywordEmbeddingClient:
    """8-dim keyword-presence embedder for unit tests. Deterministic, no
    external download. Each dimension is a keyword; a text gets 1.0 if the
    keyword appears (case-insensitive) and 0.0 otherwise; the vector is
    L2-normalised so cosine sim = inner product.
    """

    KEYWORDS = (
        "vpn",
        "outlook",
        "sharepoint",
        "badge",
        "laptop",
        "network",
        "windows",
        "drive",
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
                out[i] = out[i] / n
        return out


# ---------- Fixtures --------------------------------------------------------


@pytest.fixture
def isolated_data_dir(tmp_path: Path) -> Path:
    target = tmp_path / "data"
    shutil.copytree(REPO_DATA_DIR, target)
    return target


@pytest.fixture
def store(isolated_data_dir: Path) -> InMemoryStore:
    g = load(isolated_data_dir / "schema.yaml")
    return InMemoryStore(g, isolated_data_dir)


@pytest.fixture
def fake_embedder() -> EmbeddingClient:
    return KeywordEmbeddingClient()


@pytest.fixture
def retriever(store: InMemoryStore, fake_embedder: EmbeddingClient) -> KBRetriever:
    r = KBRetriever(store, KBIndexer(fake_embedder))
    r.build_index()
    return r


# ---------- article_text helper --------------------------------------------


def test_article_text_concatenates_title_and_body() -> None:
    a = {"short_description": "Title here", "text": "Body content."}
    out = article_text(a)
    assert "Title here" in out
    assert "Body content." in out


def test_article_text_handles_missing_fields() -> None:
    assert article_text({}) == ""
    assert "x" in article_text({"short_description": "x"})


# ---------- Indexer ---------------------------------------------------------


def test_empty_indexer_search_returns_nothing(fake_embedder: EmbeddingClient) -> None:
    idx = KBIndexer(fake_embedder)
    idx.build([])
    assert idx.search("anything") == []


def test_indexer_size_matches_built_articles(
    fake_embedder: EmbeddingClient,
) -> None:
    idx = KBIndexer(fake_embedder)
    idx.build([{"short_description": "vpn", "text": "x"}, {"short_description": "outlook", "text": "y"}])
    assert idx.size == 2


def test_indexer_returns_top_k(
    fake_embedder: EmbeddingClient,
) -> None:
    idx = KBIndexer(fake_embedder)
    idx.build(
        [
            {"short_description": "vpn issues", "text": "vpn fix", "kb_category": "Network"},
            {"short_description": "outlook crash", "text": "outlook fix", "kb_category": "Software"},
            {"short_description": "laptop slow", "text": "laptop fix", "kb_category": "Hardware"},
        ]
    )
    out = idx.search("vpn", top_k=2)
    assert len(out) == 2


# ---------- Retriever logic (no semantic dependence) ------------------------


def test_search_auto_builds_index_on_first_call(
    store: InMemoryStore, fake_embedder: EmbeddingClient
) -> None:
    """Lazy build: search() triggers build_index() if it hasn't been called yet.
    Lets the FastAPI lifespan return instantly while still serving the first
    KB query correctly (just slower)."""
    r = KBRetriever(store, KBIndexer(fake_embedder))
    assert not r.is_ready
    out = r.search("vpn")
    assert r.is_ready
    assert isinstance(out, list)


def test_build_index_publishes_only_published(
    store: InMemoryStore, fake_embedder: EmbeddingClient
) -> None:
    # Inject a draft article and verify it's excluded.
    store.create(
        "kb_knowledge",
        {
            "number": "KB0099999",
            "short_description": "Draft only",
            "text": "Draft body",
            "kb_category": "Software",
            "workflow_state": "draft",
        },
    )
    r = KBRetriever(store, KBIndexer(fake_embedder))
    r.build_index()
    assert r.size == 5  # 5 published, 1 draft excluded


def test_search_attaches_score_field(retriever: KBRetriever) -> None:
    out = retriever.search("vpn")
    assert out  # at least one hit
    assert all("_score" in r for r in out)
    assert all(isinstance(r["_score"], float) for r in out)


def test_search_top_k_bounds_results(retriever: KBRetriever) -> None:
    out = retriever.search("network", top_k=2)
    assert len(out) <= 2


def test_category_hint_filters_results(retriever: KBRetriever) -> None:
    """All returned articles must be in the hinted category."""
    out = retriever.search("anything", category_hint="Network", top_k=5)
    assert out
    assert all(r["kb_category"].lower() == "network" for r in out)


def test_category_hint_is_case_insensitive(retriever: KBRetriever) -> None:
    a = retriever.search("vpn", category_hint="network")
    b = retriever.search("vpn", category_hint="NETWORK")
    assert {r["number"] for r in a} == {r["number"] for r in b}


def test_unknown_category_yields_empty(retriever: KBRetriever) -> None:
    assert retriever.search("anything", category_hint="UnknownCategory") == []


def test_higher_score_for_better_keyword_match(retriever: KBRetriever) -> None:
    """The KeywordEmbeddingClient gives 1.0 inner product when keywords
    overlap completely. 'vpn' should score higher on the VPN article than
    the Outlook article would."""
    out = retriever.search("vpn", top_k=5)
    # KB0045678 is the VPN article and should be the top hit with our keyword embedder.
    assert out[0]["number"] == "KB0045678"


def test_results_sorted_by_score_desc(retriever: KBRetriever) -> None:
    out = retriever.search("network laptop", top_k=5)
    scores = [r["_score"] for r in out]
    assert scores == sorted(scores, reverse=True)


def test_all_categories_lists_published_categories(retriever: KBRetriever) -> None:
    cats = retriever.all_categories()
    assert {"Network", "Software", "Facilities", "Hardware"} <= set(cats)


# ---------- Real-embedder acceptance criteria from 13-tasks.md --------------
# These exercise the spec acceptance with the actual sentence-transformers
# model. First run downloads ~80MB to ~/.cache/huggingface/.


@pytest.fixture(scope="module")
def real_embedder() -> EmbeddingClient:
    pytest.importorskip("sentence_transformers")
    from src.knowledge.embeddings import SentenceTransformerEmbedding

    return SentenceTransformerEmbedding()


@pytest.fixture
def real_retriever(
    store: InMemoryStore, real_embedder: EmbeddingClient
) -> KBRetriever:
    r = KBRetriever(store, KBIndexer(real_embedder))
    r.build_index()
    return r


def test_real_outlook_query_finds_kb0045679_top_1(
    real_retriever: KBRetriever,
) -> None:
    out = real_retriever.search("Outlook crashes", top_k=1)
    assert out[0]["number"] == "KB0045679"


def test_real_vpn_query_finds_kb0045678_top_1(
    real_retriever: KBRetriever,
) -> None:
    out = real_retriever.search("VPN not working", top_k=1)
    assert out[0]["number"] == "KB0045678"


def test_real_index_builds_in_under_two_seconds(
    store: InMemoryStore, real_embedder: EmbeddingClient
) -> None:
    """Acceptance: index for 5 articles builds in under 2s.
    Excludes model load (handled by the module-scoped fixture).
    """
    r = KBRetriever(store, KBIndexer(real_embedder))
    t0 = time.perf_counter()
    r.build_index()
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"build_index took {elapsed:.2f}s"


def test_real_category_hint_narrows_to_network(
    real_retriever: KBRetriever,
) -> None:
    """Without hint, a generic 'help' query should return mixed categories.
    With Network hint, only Network articles come back."""
    out = real_retriever.search("help me with my issue", category_hint="Network", top_k=5)
    assert all(r["kb_category"] == "Network" for r in out)
