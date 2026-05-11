from __future__ import annotations

from typing import Any

import faiss

from src.knowledge.embeddings import EmbeddingClient

Article = dict[str, Any]


def article_text(article: Article) -> str:
    """Combine title + body for embedding. Title is repeated (light boost)."""
    title = article.get("short_description", "")
    body = article.get("text", "")
    return f"{title}\n\n{title}\n\n{body}".strip()


class KBIndexer:
    """FAISS-backed index over a fixed set of KB articles.

    Insertion order is preserved: `_records[i]` corresponds to row `i` in
    the FAISS index.
    """

    def __init__(self, embedding_client: EmbeddingClient) -> None:
        self._client = embedding_client
        self._index: faiss.Index | None = None
        self._records: list[Article] = []

    @property
    def size(self) -> int:
        return len(self._records)

    def build(self, articles: list[Article]) -> None:
        """Embed `articles` and build a flat inner-product index over them.
        Replaces any existing index.
        """
        self._records = list(articles)
        if not articles:
            self._index = faiss.IndexFlatIP(self._client.dimension)
            return
        texts = [article_text(a) for a in articles]
        embs = self._client.embed(texts)
        index = faiss.IndexFlatIP(self._client.dimension)
        index.add(embs)
        self._index = index

    def search(
        self,
        query: str,
        top_k: int = 3,
        category_hint: str | None = None,
    ) -> list[tuple[Article, float]]:
        """Return up to top_k (article, similarity) pairs sorted desc by score.

        If `category_hint` is provided, results are filtered to articles whose
        `kb_category` matches case-insensitively. Filtering happens after the
        FAISS lookup so the score reflects semantic relevance over the full
        index, not over the category subset.
        """
        if self._index is None or not self._records:
            return []
        q_emb = self._client.embed([query])
        scores, indices = self._index.search(q_emb, len(self._records))

        out: list[tuple[Article, float]] = []
        for idx_arr_score, idx_arr_idx in zip(scores[0], indices[0], strict=True):
            if idx_arr_idx < 0:
                continue
            rec = self._records[int(idx_arr_idx)]
            if category_hint and not _category_matches(rec, category_hint):
                continue
            out.append((rec, float(idx_arr_score)))
            if len(out) >= top_k:
                break
        return out


def _category_matches(article: Article, hint: str) -> bool:
    cat = str(article.get("kb_category", "")).strip().lower()
    return cat == hint.strip().lower()
