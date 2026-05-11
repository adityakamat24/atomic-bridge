from __future__ import annotations

from typing import Protocol

import numpy as np


class EmbeddingClient(Protocol):
    """Provider-agnostic embedding interface."""

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: list[str]) -> np.ndarray: ...


# Hard-coded dimensions for models we ship with — lets us answer
# `.dimension` without instantiating the model. Falls back to a
# real load for unknown models.
_KNOWN_DIMS: dict[str, int] = {
    "all-MiniLM-L6-v2": 384,
    "all-MiniLM-L12-v2": 384,
    "all-mpnet-base-v2": 768,
}


class SentenceTransformerEmbedding:
    """Local sentence-transformers backend with **lazy model loading**.

    The model is NOT loaded in __init__. First `embed()` call (or first
    `dimension` access for an unknown model) triggers the load. This keeps
    backend startup fast — critical on platforms like Fly.io where slow
    startup races health checks.

    Default model `all-MiniLM-L6-v2` (384-dim, ~80 MB) is pre-cached in
    the Docker image at /home/app/.cache, so the first load takes a few
    seconds. Cold boot of the backend itself takes ~2s.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model: object | None = None
        self._dim: int | None = _KNOWN_DIMS.get(model_name)

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        # Imported lazily so test files that monkeypatch this module don't
        # pay the import cost.
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(self.model_name)
        # ST renamed the dim accessor in v3.x; support either.
        if hasattr(model, "get_embedding_dimension"):
            dim = model.get_embedding_dimension()
        else:
            dim = model.get_sentence_embedding_dimension()
        if dim is None:
            raise RuntimeError(
                f"sentence-transformers returned no dimension for {self.model_name}"
            )
        self._model = model
        self._dim = int(dim)

    @property
    def dimension(self) -> int:
        if self._dim is None:
            self._ensure_loaded()
        assert self._dim is not None
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        self._ensure_loaded()
        assert self._model is not None
        embs = self._model.encode(  # type: ignore[attr-defined]
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(embs, dtype=np.float32)
