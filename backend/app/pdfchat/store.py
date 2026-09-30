"""Tiny in-memory vector store.

No external vector DB — the prototype corpus is small, so cosine over a plain
list is fast enough and dependency-free. Persistence lives in service.py, which
caches per PDF (keyed by each file's fingerprint plus the embedding model) so
adding or changing one document only re-embeds that document.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


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

    def search(self, query_vec: list[float], k: int = 8) -> list[tuple[dict, float]]:
        scored = [
            (self.chunks[i], _cosine(query_vec, emb))
            for i, emb in enumerate(self.embeddings)
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]
