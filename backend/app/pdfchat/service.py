"""Chat-with-PDFs RAG service.

Builds (and caches) a vector index over the PDF corpus, then answers a question
by retrieving the most relevant chunks and asking the LLM to answer ONLY from
them, citing each fact with a [n] marker. Every [n] is mapped back to its source
chunk's {pdf, page, line boxes} so the UI can open that PDF at that page and
highlight the exact cited line(s).
"""
from __future__ import annotations

import json
import logging
import re
from threading import Lock
from typing import Any

from app.config import get_settings
from app.nl_to_sql.client import get_llm_client
from app.pdfchat.ingest import extract_chunks
from app.pdfchat.source import get_pdf_source
from app.pdfchat.store import VectorStore

logger = logging.getLogger(__name__)

_store: VectorStore | None = None
_LOCK = Lock()
TOP_K = 8
# Bump when the chunk/box format changes so the on-disk cache is rebuilt.
INDEX_VERSION = 3

_SYSTEM = (
    "You answer questions using ONLY the numbered SOURCES provided (excerpts from "
    "PDF documents). Rules:\n"
    "- Base every statement strictly on the sources. Do NOT use outside knowledge.\n"
    "- After each fact, cite the source it came from with its number in square "
    "brackets, e.g. [2]. Cite every sentence that uses a source. You may cite more "
    "than one, e.g. [1][3].\n"
    "- If the sources do not contain the answer, say so plainly and set found=false.\n"
    "- Be concise and factual. Mirror the user's language (English/Hindi/Hinglish).\n"
    'Return JSON: {"answer": "<answer text with [n] citation markers>", '
    '"found": true|false}.'
)


def _cache_path():
    # Per-PDF cache so adding/removing one PDF only re-processes that PDF.
    return get_settings().pdf_index_path / "pdfs.json"


def _fingerprint(src) -> str:
    s = get_settings()
    return f"v{INDEX_VERSION}|{src.corpus_fingerprint()}|{s.openai_embedding_model}"


def _load_pdf_cache() -> dict[str, Any]:
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - corrupt cache -> rebuild
        return {}


def _save_pdf_cache(data: dict[str, Any]) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so the swap is atomic: a crash mid-write would
        # otherwise leave a truncated file, and recovery means re-embedding the
        # entire corpus (the most expensive operation in the app).
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        logger.warning("Could not write PDF cache", exc_info=True)


def get_index(force: bool = False) -> VectorStore:
    """Return the current index, incrementally (re)building only PDFs that are new,
    changed, or removed since the last build. Cheap when nothing changed."""
    global _store
    src = get_pdf_source()
    want_fp = _fingerprint(src)

    if not force and _store is not None and _store.meta.get("fingerprint") == want_fp:
        return _store

    # Serialise rebuilds. Without this, concurrent cold requests would each
    # re-embed the whole corpus instead of one build serving all of them.
    with _LOCK:
        if not force and _store is not None and _store.meta.get("fingerprint") == want_fp:
            return _store
        _store = _build_incremental(src, want_fp, force)
        return _store


def _build_incremental(src, want_fp: str, force: bool) -> VectorStore:
    s = get_settings()
    llm = get_llm_client()
    cached = {} if force else _load_pdf_cache()
    # If the embedding model changed, everything must be re-embedded.
    prev_pdfs: dict[str, Any] = {}
    if cached.get("embedding_model") == s.openai_embedding_model:
        prev_pdfs = cached.get("pdfs", {})

    try:
        refs = src.list_pdfs()
    except Exception:  # noqa: BLE001 - source unreachable -> keep whatever we had
        logger.warning("PDF source unavailable", exc_info=True)
        refs = []

    new_pdfs: dict[str, Any] = {}
    all_chunks: list[dict[str, Any]] = []
    all_emb: list[list[float]] = []
    reused = built = 0
    for ref in refs:
        prev = prev_pdfs.get(ref.id)
        if prev and prev.get("fp") == ref.fingerprint:
            entry = prev
            reused += 1
        else:
            try:
                path = src.get_local_path(ref.id)
                chunks = [c.to_dict() for c in extract_chunks(ref.id, ref.name, path)]
                emb = llm.embed([c["text"] for c in chunks]) if chunks else []
                entry = {"fp": ref.fingerprint, "name": ref.name, "chunks": chunks, "embeddings": emb}
                built += 1
                logger.info("Indexed %s (%d chunks)", ref.name, len(chunks))
            except Exception:  # noqa: BLE001 - one bad PDF shouldn't break the rest
                logger.warning("Failed to index %s", ref.name, exc_info=True)
                continue
        new_pdfs[ref.id] = entry
        all_chunks.extend(entry["chunks"])
        all_emb.extend(entry["embeddings"])

    if refs:
        logger.info("PDF index: %d reused, %d (re)built, %d total", reused, built, len(new_pdfs))
    _save_pdf_cache({"embedding_model": s.openai_embedding_model, "pdfs": new_pdfs})
    return VectorStore(chunks=all_chunks, embeddings=all_emb, meta={"fingerprint": want_fp})


def list_documents() -> list[dict[str, Any]]:
    """Available PDFs with page counts. Ensures the index is loaded (from cache if
    present) so page counts are populated; a first-ever call with no cache builds it."""
    src = get_pdf_source()
    pages_by_id: dict[str, int] = {}
    try:
        index = get_index()
        for c in index.chunks:
            pages_by_id[c["pdf_id"]] = max(pages_by_id.get(c["pdf_id"], 0), c["page"])
    except Exception:  # noqa: BLE001 - still list the files even if indexing fails
        logger.warning("Index unavailable while listing documents", exc_info=True)
    return [
        {"id": r.id, "name": r.name, "pages": pages_by_id.get(r.id, 0)}
        for r in src.list_pdfs()
    ]


_CITE_RE = re.compile(r"\[(\d+)\]")


def answer(question: str) -> dict[str, Any]:
    question = (question or "").strip()
    if not question:
        return {"answer": "Please ask a question.", "citations": [], "found": False}

    index = get_index()
    if not index.chunks:
        return {
            "answer": "No PDFs are available yet. Add PDFs to the corpus and try again.",
            "citations": [],
            "found": False,
        }

    llm = get_llm_client()
    qvec = llm.embed([question])[0]
    hits = index.search(qvec, k=TOP_K)

    # Numbered sources (1-based) fed to the model.
    sources_block = "\n".join(
        f'[{n}] "{c["text"]}"  (source: {c["pdf_name"]}, page {c["page"]})'
        for n, (c, _score) in enumerate(hits, start=1)
    )
    user = f"SOURCES:\n{sources_block}\n\nQUESTION: {question}"
    try:
        out = llm.generate_json(_SYSTEM, user)
    except Exception:  # noqa: BLE001
        logger.warning("PDF chat generation failed", exc_info=True)
        return {"answer": "I couldn't answer that just now. Please try again.", "citations": [], "found": False}

    answer_text = str(out.get("answer") or "").strip()
    found = bool(out.get("found", True))

    # The [n] markers hold the SOURCE's retrieval rank (1..K). Renumber them to
    # sequential display numbers (1, 2, 3…) in order of first appearance, so a
    # single-source answer shows [1] rather than e.g. [4]. Rewrite the markers in
    # the answer text to match, and drop any that reference a non-existent source.
    display_no: dict[int, int] = {}
    order: list[int] = []
    for m in _CITE_RE.finditer(answer_text):
        n = int(m.group(1))
        if n < 1 or n > len(hits) or n in display_no:
            continue
        display_no[n] = len(order) + 1
        order.append(n)

    def _renumber(m):
        n = int(m.group(1))
        return f"[{display_no[n]}]" if n in display_no else ""

    answer_text = _CITE_RE.sub(_renumber, answer_text).strip()

    used = []
    for old_n in order:
        chunk, score = hits[old_n - 1]
        snippet = chunk["text"]
        used.append({
            "n": display_no[old_n],
            "pdf_id": chunk["pdf_id"],
            "pdf_name": chunk["pdf_name"],
            "page": chunk["page"],
            "page_width": chunk["page_width"],
            "page_height": chunk["page_height"],
            "bbox": chunk["bbox"],
            "lines": chunk["lines"],
            "snippet": (snippet[:200] + "…") if len(snippet) > 200 else snippet,
            "score": round(float(score), 3),
        })

    return {"answer": answer_text, "citations": used, "found": found}
