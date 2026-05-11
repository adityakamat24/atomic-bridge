from __future__ import annotations

from typing import Any

from src.core.data_store import DataStore, Filter
from src.knowledge.indexer import KBIndexer

Article = dict[str, Any]


class KBRetriever:
    """Hybrid KB retrieval.

    Pulls articles from the DataStore at index-build time, embeds them via
    the indexer, and exposes a single `search` method that combines a
    `kb_category` filter with semantic ranking.

    The build step is deliberately separate so the FastAPI app can call it
    once at startup (see `/health/ready`).
    """

    def __init__(self, store: DataStore, indexer: KBIndexer) -> None:
        self._store = store
        self._indexer = indexer
        self._built = False

    @property
    def is_ready(self) -> bool:
        return self._built

    @property
    def size(self) -> int:
        return self._indexer.size

    def build_index(self) -> None:
        articles = self._store.find("kb_knowledge", filters=[], limit=10_000)
        published = [
            a
            for a in articles
            if str(a.get("workflow_state", "published")).lower() == "published"
        ]
        self._indexer.build(published)
        self._built = True

    def search(
        self,
        query: str,
        category_hint: str | None = None,
        top_k: int = 3,
    ) -> list[Article]:
        """Returns up to `top_k` article records, each with an extra
        `_score` float (cosine similarity, higher = better). Empty list if
        nothing matched.

        Auto-builds the index on first call (lazy). Subsequent calls reuse it.
        """
        if not self._built:
            self.build_index()
        hits = self._indexer.search(query, top_k=top_k, category_hint=category_hint)
        out: list[Article] = []
        for rec, score in hits:
            row = dict(rec)
            row["_score"] = score
            out.append(row)
        return out

    # ----- diagnostics ----------------------------------------------------

    def all_categories(self) -> list[str]:
        """Distinct `kb_category` values currently indexed. Used by the
        planner's preprocessor to suggest valid hints.
        """
        cats: list[str] = []
        seen: set[str] = set()
        # We can't enumerate the indexer's records directly — go back to the store.
        for a in self._store.find(
            "kb_knowledge", filters=[Filter(field="workflow_state", operator="eq", value="published")]
        ):
            c = str(a.get("kb_category", "")).strip()
            if c and c not in seen:
                seen.add(c)
                cats.append(c)
        return cats
