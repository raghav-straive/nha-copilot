"""OCR line grouping — the part that decides what a citation highlight covers.

Both engines emit word boxes that have to be grouped into lines and normalised to
page fractions. If grouping merges two visual lines, the highlight covers text the
answer never cited. These tests use synthetic word boxes, so no Tesseract binary
and no Vision credentials are needed.
"""
from types import SimpleNamespace

import pytest

from app.pdfchat import ocr


# ---- Tesseract path -------------------------------------------------------


def _tsv(words):
    """Build a pytesseract image_to_data DICT from (block, par, line, x, y, w, h, text)."""
    keys = ("block_num", "par_num", "line_num", "left", "top", "width", "height", "text")
    d = {k: [] for k in keys}
    d["conf"] = []
    for block, par, line, x, y, w, h, text in words:
        vals = (block, par, line, x, y, w, h, text)
        for k, v in zip(keys, vals):
            d[k].append(v)
        d["conf"].append(90)
    return d


def test_tesseract_words_group_into_lines_in_reading_order():
    d = _tsv([
        (1, 1, 1, 10, 10, 30, 12, "Total"),
        (1, 1, 1, 50, 10, 40, 12, "ABHA"),
        (1, 1, 2, 10, 40, 60, 12, "created"),
    ])
    lines = ocr._lines_from_tsv(d, 200.0, 100.0)
    assert [l.text for l in lines] == ["Total ABHA", "created"]


def test_tesseract_boxes_are_page_fractions():
    d = _tsv([(1, 1, 1, 50, 25, 50, 25, "word")])
    (line,) = ocr._lines_from_tsv(d, 200.0, 100.0)
    assert line.x0 == pytest.approx(0.25)
    assert line.top == pytest.approx(0.25)
    assert line.x1 == pytest.approx(0.50)
    assert line.bottom == pytest.approx(0.50)
    assert all(0.0 <= v <= 1.0 for v in (line.x0, line.top, line.x1, line.bottom))


def test_tesseract_low_confidence_words_are_dropped():
    d = _tsv([(1, 1, 1, 10, 10, 30, 12, "good")])
    d["text"].append("garbled")
    d["conf"].append(5)  # below the confidence floor
    for k in ("block_num", "par_num", "line_num"):
        d[k].append(1)
    for k, v in (("left", 50), ("top", 10), ("width", 30), ("height", 12)):
        d[k].append(v)
    (line,) = ocr._lines_from_tsv(d, 200.0, 100.0)
    assert line.text == "good"


def test_tesseract_words_are_ordered_by_x_not_input_order():
    d = _tsv([
        (1, 1, 1, 90, 10, 30, 12, "third"),
        (1, 1, 1, 10, 10, 30, 12, "first"),
        (1, 1, 1, 50, 10, 30, 12, "second"),
    ])
    (line,) = ocr._lines_from_tsv(d, 200.0, 100.0)
    assert line.text == "first second third"


def test_tesseract_empty_input_yields_no_lines():
    assert ocr._lines_from_tsv(_tsv([]), 200.0, 100.0) == []


# ---- Vision path ----------------------------------------------------------


def _vision_response(words, page_w=200, page_h=100):
    """Fake a Vision document_text_detection response from (x0, y0, x1, y1, text)."""
    def word(x0, y0, x1, y1, text):
        verts = [
            SimpleNamespace(x=x0, y=y0), SimpleNamespace(x=x1, y=y0),
            SimpleNamespace(x=x1, y=y1), SimpleNamespace(x=x0, y=y1),
        ]
        return SimpleNamespace(
            symbols=[SimpleNamespace(text=c) for c in text],
            bounding_box=SimpleNamespace(vertices=verts),
        )

    para = SimpleNamespace(words=[word(*w) for w in words])
    page = SimpleNamespace(width=page_w, height=page_h,
                           blocks=[SimpleNamespace(paragraphs=[para])])
    return SimpleNamespace(
        error=SimpleNamespace(message=""),
        full_text_annotation=SimpleNamespace(pages=[page]),
    )


@pytest.fixture
def vision(monkeypatch):
    """Patch out the Vision SDK and client so grouping can be tested offline."""
    def _run(words, **kw):
        resp = _vision_response(words, **kw)
        client = SimpleNamespace(document_text_detection=lambda **_: resp)
        monkeypatch.setattr(ocr, "_vision_client", lambda: client)
        fake_sdk = SimpleNamespace(
            Image=lambda **kw: None,
            ImageContext=lambda **kw: None,
        )
        monkeypatch.setitem(
            __import__("sys").modules, "google.cloud.vision", fake_sdk
        )
        return ocr._vision_lines(b"png")
    return _run


def test_vision_groups_two_lines_separately(vision):
    lines = vision([
        (10, 10, 40, 30, "Total"),
        (50, 10, 90, 30, "ABHA"),
        (10, 40, 70, 60, "created"),
    ])
    assert [l.text for l in lines] == ["Total ABHA", "created"]


def test_vision_a_tall_word_does_not_swallow_the_next_line(vision):
    """A tall glyph (superscript, table rule, inline mark) used to stretch its
    row's vertical span until the following line's centre fell inside it, merging
    two lines into one — so a citation highlight covered text it never cited."""
    lines = vision([
        (10, 10, 40, 30, "Scan"),
        (50, 10, 80, 50, "&"),      # unusually tall
        (10, 35, 90, 55, "Share"),  # the NEXT line
    ])
    assert len(lines) == 2, f"expected two lines, got {[l.text for l in lines]}"
    assert lines[1].text == "Share"


def test_vision_boxes_are_page_fractions(vision):
    (line,) = vision([(50, 25, 100, 50, "word")])
    assert line.x0 == pytest.approx(0.25)
    assert line.top == pytest.approx(0.25)
    assert line.x1 == pytest.approx(0.50)
    assert line.bottom == pytest.approx(0.50)


def test_vision_orders_lines_top_to_bottom(vision):
    lines = vision([
        (10, 70, 60, 90, "last"),
        (10, 10, 60, 30, "first"),
        (10, 40, 60, 60, "middle"),
    ])
    assert [l.text for l in lines] == ["first", "middle", "last"]


def test_vision_no_text_yields_no_lines(vision):
    assert vision([]) == []


def test_vision_reports_an_api_error(monkeypatch):
    resp = SimpleNamespace(
        error=SimpleNamespace(message="PERMISSION_DENIED"),
        full_text_annotation=None,
    )
    client = SimpleNamespace(document_text_detection=lambda **_: resp)
    monkeypatch.setattr(ocr, "_vision_client", lambda: client)
    monkeypatch.setitem(
        __import__("sys").modules, "google.cloud.vision",
        SimpleNamespace(Image=lambda **kw: None, ImageContext=lambda **kw: None),
    )
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        ocr._vision_lines(b"png")


def test_vision_zero_page_dimensions_do_not_divide_by_zero(vision):
    lines = vision([(0, 0, 10, 10, "x")], page_w=0, page_h=0)
    assert len(lines) == 1  # guarded with `or 1.0`


# ---- engine selection -----------------------------------------------------


def test_no_pages_short_circuits_without_touching_an_engine(monkeypatch):
    def explode():
        raise AssertionError("must not resolve an engine for zero pages")

    monkeypatch.setattr(ocr, "_resolve_engine", explode)
    assert ocr.ocr_document("nonexistent.pdf", []) == {}


def test_no_engine_available_returns_empty_rather_than_raising(monkeypatch):
    monkeypatch.setattr(ocr, "_resolve_engine", lambda: None)
    assert ocr.ocr_document("nonexistent.pdf", [0, 1]) == {}
