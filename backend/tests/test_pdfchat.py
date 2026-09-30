"""Chat-with-PDFs: vector search and citation renumbering.

Citation renumbering is the part users see most directly — a wrong number sends
them to the wrong page — so it gets the most attention here.
"""
import pytest

from app.pdfchat import service as ps
from app.pdfchat.store import VectorStore


# ---- vector search ----


def _chunk(n):
    return {
        "chunk_id": f"c{n}", "pdf_id": "doc", "pdf_name": "doc.pdf", "page": n,
        "page_width": 600.0, "page_height": 800.0, "text": f"passage {n}",
        "bbox": [0, 0, 1, 1], "lines": [],
    }


def test_search_ranks_the_nearest_passage_first():
    store = VectorStore(
        chunks=[_chunk(1), _chunk(2), _chunk(3)],
        embeddings=[[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]],
    )
    hits = store.search([1.0, 0.0], k=3)
    assert [c["chunk_id"] for c, _ in hits] == ["c1", "c3", "c2"]


def test_search_scores_are_descending():
    store = VectorStore(
        chunks=[_chunk(i) for i in range(5)],
        embeddings=[[1.0, 0.0], [0.9, 0.1], [0.5, 0.5], [0.1, 0.9], [0.0, 1.0]],
    )
    scores = [s for _, s in store.search([1.0, 0.0], k=5)]
    assert scores == sorted(scores, reverse=True)


def test_search_respects_k():
    store = VectorStore(
        chunks=[_chunk(i) for i in range(10)],
        embeddings=[[float(i), 1.0] for i in range(10)],
    )
    assert len(store.search([1.0, 1.0], k=3)) == 3


def test_search_handles_k_larger_than_the_corpus():
    store = VectorStore(chunks=[_chunk(1)], embeddings=[[1.0, 0.0]])
    assert len(store.search([1.0, 0.0], k=50)) == 1


def test_search_on_an_empty_store_returns_nothing():
    assert VectorStore().search([1.0, 0.0], k=5) == []


def test_search_survives_a_zero_vector():
    store = VectorStore(chunks=[_chunk(1)], embeddings=[[0.0, 0.0]])
    hits = store.search([0.0, 0.0], k=1)
    assert len(hits) == 1  # no division-by-zero, no crash


def test_vectorised_and_python_paths_agree():
    # The NumPy path is an optimization; it must not change the ranking.
    store = VectorStore(
        chunks=[_chunk(i) for i in range(6)],
        embeddings=[[1.0, 0.2], [0.3, 0.9], [0.8, 0.1], [0.1, 1.0], [0.6, 0.6], [0.2, 0.4]],
    )
    q = [0.9, 0.3]
    fast = [c["chunk_id"] for c, _ in store.search(q, k=6)]
    slow = [c["chunk_id"] for c, _ in store._search_python(q, 6)]
    assert fast == slow


# ---- citation renumbering ----


class FakeLLM:
    def __init__(self, answer, found=True):
        self.answer = answer
        self.found = found

    def generate_json(self, system, user):
        return {"answer": self.answer, "found": self.found}

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


@pytest.fixture
def indexed(monkeypatch):
    """An index of 4 passages, with the LLM and embeddings faked out."""
    store = VectorStore(
        chunks=[_chunk(1), _chunk(2), _chunk(3), _chunk(4)],
        embeddings=[[1.0, 0.0], [0.9, 0.1], [0.8, 0.2], [0.7, 0.3]],
    )
    monkeypatch.setattr(ps, "get_index", lambda force=False: store)

    def _use(answer, found=True):
        monkeypatch.setattr(ps, "get_llm_client", lambda: FakeLLM(answer, found))
        return store

    return _use


def test_single_citation_is_renumbered_to_one(indexed):
    # The model cites by retrieval rank; a lone [3] must display as [1].
    indexed("The figure is 42 [3].")
    out = ps.answer("what is the figure?")
    assert "[1]" in out["answer"]
    assert "[3]" not in out["answer"]
    assert [c["n"] for c in out["citations"]] == [1]


def test_citations_are_numbered_in_order_of_first_appearance(indexed):
    indexed("First [4]. Second [2]. Third [4] again.")
    out = ps.answer("q")
    assert out["answer"] == "First [1]. Second [2]. Third [1] again."
    assert [c["n"] for c in out["citations"]] == [1, 2]


def test_out_of_range_citations_are_dropped(indexed):
    # Only 4 sources exist; [99] refers to nothing and must not be shown.
    indexed("Real [1] and invented [99].")
    out = ps.answer("q")
    assert "[99]" not in out["answer"]
    assert len(out["citations"]) == 1


def test_citations_map_back_to_the_right_page(indexed):
    indexed("Fact [2].")
    out = ps.answer("q")
    c = out["citations"][0]
    assert c["pdf_name"] == "doc.pdf"
    assert c["page"] == 2, "citation [2] is the 2nd-ranked passage, which is page 2"
    assert "page_width" in c and "lines" in c, "viewer needs geometry to highlight"


def test_not_found_is_reported_honestly(indexed):
    indexed("The documents do not cover that.", found=False)
    out = ps.answer("something absent")
    assert out["found"] is False


def test_empty_question_short_circuits(indexed):
    indexed("unused")
    out = ps.answer("   ")
    assert out["found"] is False
    assert out["citations"] == []


def test_no_documents_says_so_rather_than_failing(monkeypatch):
    monkeypatch.setattr(ps, "get_index", lambda force=False: VectorStore())
    out = ps.answer("anything")
    assert out["found"] is False
    assert "No PDFs" in out["answer"]


def test_llm_failure_degrades_gracefully(indexed, monkeypatch):
    indexed("unused")

    class Broken:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

        def generate_json(self, *a, **k):
            raise RuntimeError("upstream down")

    monkeypatch.setattr(ps, "get_llm_client", lambda: Broken())
    out = ps.answer("q")
    assert out["found"] is False
    assert out["citations"] == []


def test_snippets_are_truncated(indexed):
    store = indexed("Fact [1].")
    store.chunks[0]["text"] = "x" * 500
    out = ps.answer("q")
    assert len(out["citations"][0]["snippet"]) <= 201
