"""Tiny in-memory vector store.

No external vector DB — the corpus is small enough that a single matrix-vector
product beats the operational cost of running one. Persistence lives in
service.py, which caches per PDF (keyed by each file's fingerprint plus the
embedding model) so adding or changing one document only re-embeds that one.

Similarity was a pure-Python loop over every passage on every search. It is now
one NumPy product against a matrix whose rows are pre-normalised at build time,
so the per-query work is a single BLAS call. NumPy is optional: without it the
store falls back to the original loop, which keeps the prototype installable in
a minimal environment.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

try:  # NumPy is a hard win here but not worth a hard dependency.
    import numpy as _np
except ImportError:  # pragma: no cover - exercised only in minimal installs
    _np = None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


@dataclass
class VectorStore:
    chunks: list[dict[str, Any]] = field(default_factory=list)
    embeddings: list[list[float]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._matrix = None
        if _np is None or not self.embeddings:
            return
        try:
            mat = _np.asarray(self.embeddings, dtype=_np.float32)
            if mat.ndim != 2:  # ragged embeddings -> fall back to the loop
                return
            # Pre-normalise the rows: cosine then reduces to a dot product, so
            # nothing per-query depends on the passage side.
            norms = _np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._matrix = mat / norms
        except (ValueError, TypeError):  # pragma: no cover
            self._matrix = None

    def search(self, query_vec: list[float], k: int = 8) -> list[tuple[dict, float]]:
        if not self.chunks or not self.embeddings:
            return []
        if self._matrix is None:
            return self._search_python(query_vec, k)

        q = _np.asarray(query_vec, dtype=_np.float32)
        if q.shape[0] != self._matrix.shape[1]:  # dimension change -> be safe
            return self._search_python(query_vec, k)
        q = q / (float(_np.linalg.norm(q)) or 1.0)
        scores = self._matrix @ q

        n = min(k, scores.shape[0])
        if n >= scores.shape[0]:
            order = _np.argsort(-scores)
        else:
            # argpartition finds the top n without sorting the whole array,
            # then we sort just those n.
            part = _np.argpartition(-scores, n - 1)[:n]
            order = part[_np.argsort(-scores[part])]
        return [(self.chunks[int(i)], float(scores[int(i)])) for i in order]

    def _search_python(self, query_vec: list[float], k: int) -> list[tuple[dict, float]]:
        scored = [
            (self.chunks[i], _cosine(query_vec, emb))
            for i, emb in enumerate(self.embeddings)
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]
