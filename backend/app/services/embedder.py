"""768-dimension sentence embeddings.

Production backend: a RoBERTa sentence encoder via `sentence-transformers`
(default `sentence-transformers/all-distilroberta-v1`, 768-dim). The `hash` backend is a
deterministic feature-hashing embedder for tests and fully offline demos; it captures lexical
overlap only. The two backends produce different vector spaces — never mix them in one database.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from typing import Protocol

from app.config import get_settings
from app.models import EMBEDDING_DIM

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_one(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Signed feature hashing of word unigrams + character trigrams, L2-normalised."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim
        self.name = f"hash-{dim}"

    def _features(self, text: str) -> list[tuple[str, float]]:
        words = _WORD.findall(text.lower())
        feats: list[tuple[str, float]] = [(f"w:{w}", 1.0) for w in words]
        for w in words:
            padded = f"#{w}#"
            feats.extend((f"c:{padded[i:i + 3]}", 0.5) for i in range(len(padded) - 2))
        return feats

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for feat, weight in self._features(text):
            digest = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign * weight
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            vec[0] = 1.0  # avoid a zero vector: cosine distance is undefined for it
            return vec
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, deferred

        logger.info("Loading embedding model %s", model_name)
        self._model = SentenceTransformer(model_name, device="cpu")
        dim = self._model.get_sentence_embedding_dimension()
        if dim != EMBEDDING_DIM:
            raise ValueError(
                f"Embedding model {model_name!r} outputs {dim} dims but the schema is "
                f"VECTOR({EMBEDDING_DIM}). Use a 768-dim RoBERTa model such as "
                "sentence-transformers/all-distilroberta-v1."
            )
        self.dim = dim
        self.name = model_name
        self._lock = threading.Lock()  # torch modules are not guaranteed thread-safe

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            vectors = self._model.encode(
                texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
            )
        return [v.astype(float).tolist() for v in vectors]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


_instance: Embedder | None = None
_instance_lock = threading.Lock()


def get_embedder() -> Embedder:
    """Process-wide singleton. Fails loudly instead of silently switching vector spaces."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                settings = get_settings()
                if settings.embedder_backend == "hash":
                    _instance = HashingEmbedder()
                else:
                    _instance = SentenceTransformerEmbedder(settings.embedding_model)
    return _instance


def set_embedder(embedder: Embedder | None) -> None:
    """Override the singleton (tests)."""
    global _instance
    _instance = embedder


def to_pgvector_literal(vec: list[float]) -> str:
    """Serialize for `CAST(:query_vec AS vector)`; validates shape and finiteness."""
    if len(vec) != EMBEDDING_DIM:
        raise ValueError(f"expected {EMBEDDING_DIM} dims, got {len(vec)}")
    if not all(math.isfinite(x) for x in vec):
        raise ValueError("embedding contains non-finite values")
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
