from __future__ import annotations

from typing import Protocol

import numpy as np


class EmbeddingClient(Protocol):
    """Provider-agnostic embedding interface.

    The Phase-3 default is a local sentence-transformers model. The same
    protocol can wrap an OpenAI / Voyage / Cohere call later — the indexer
    and retriever never see the difference.
    """

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: list[str]) -> np.ndarray:
        """Returns a (len(texts), dimension) float32 array. Vectors should
        be L2-normalised so cosine similarity = inner product (FAISS IP).
        """


class SentenceTransformerEmbedding:
    """Local sentence-transformers backend. Default model `all-MiniLM-L6-v2`
    (384-dim, ~80 MB on first download to ~/.cache/huggingface/).

    The model is loaded eagerly so the first request after startup is fast.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        # Imported lazily so test files that monkeypatch this module don't
        # pay the import cost.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        # ST renamed `get_sentence_embedding_dimension` -> `get_embedding_dimension`
        # in v3.x. Support either method without touching the user.
        if hasattr(self._model, "get_embedding_dimension"):
            dim = self._model.get_embedding_dimension()
        else:
            dim = self._model.get_sentence_embedding_dimension()
        if dim is None:
            raise RuntimeError(f"sentence-transformers returned no dimension for {model_name}")
        self._dim = int(dim)
        self.model_name = model_name

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        embs = self._model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(embs, dtype=np.float32)
