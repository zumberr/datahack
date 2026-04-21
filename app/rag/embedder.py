"""
Sentence-transformers wrapper. Loads the multilingual-e5 model once (module-level singleton).

The e5 family expects specific prefixes:
  - "passage: <text>" for indexed documents
  - "query: <text>"   for search queries
We apply the prefix here so callers don't have to worry about it.
"""
from __future__ import annotations

import threading
from collections.abc import Sequence

import numpy as np

from app.config import get_settings

_model = None
_lock = threading.Lock()


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        from sentence_transformers import SentenceTransformer
        settings = get_settings()
        _model = SentenceTransformer(settings.embedding_model)
    return _model


def embed_passages(texts: Sequence[str], batch_size: int = 16) -> np.ndarray:
    """Embed documents (uses the 'passage:' prefix)."""
    if not texts:
        return np.zeros((0, get_settings().embedding_dim), dtype=np.float32)
    model = _load_model()
    prefixed = [f"passage: {t}" for t in texts]
    vecs = model.encode(
        prefixed,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vecs.astype(np.float32)


def embed_query(text: str) -> np.ndarray:
    """Embed a single search query (uses the 'query:' prefix)."""
    model = _load_model()
    vec = model.encode(
        [f"query: {text}"],
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vec[0].astype(np.float32)
