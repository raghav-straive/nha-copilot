"""Tiny in-memory vector store with a JSON disk cache.

No external vector DB — the prototype corpus is small, so cosine over a plain list
is fast enough and dependency-free. The index (chunks + embeddings) is cached to
disk keyed by the corpus fingerprint + embedding model, so we only re-embed when
the PDFs or the model change.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
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

    # ---- persistence ----
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"meta": self.meta, "chunks": self.chunks, "embeddings": self.embeddings}
        path.write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "VectorStore | None":
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                chunks=data.get("chunks", []),
                embeddings=data.get("embeddings", []),
                meta=data.get("meta", {}),
            )
        except Exception:  # noqa: BLE001 - a corrupt cache should just trigger a rebuild
            return None
