"""PDF ingestion: extract per-line text with bounding boxes, then chunk.

Uses pdfplumber for the text layer, falling back to OCR (ocr.py) for scanned pages.
All box coordinates are stored as **page fractions (0..1)** with a top-left origin —
NOT points. Fractions are invariant to point-space quirks (some PDFs report a
different page width across libraries), so a fraction × the rendered page size in
the viewer lands the highlight on the exact cited line every time.

Each chunk carries: the source pdf, 1-based page, page width/height (points, for
reference only), the joined text, and the list of fractional line boxes it spans.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)

# Chunking: group consecutive lines up to ~these limits (keeps a citation tight
# enough to highlight, big enough to be meaningful for retrieval).
MAX_CHUNK_CHARS = 700
MAX_CHUNK_LINES = 8
LINE_Y_TOLERANCE = 3.0  # words within this many points of `top` are the same line


@dataclass
class LineBox:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass
class Chunk:
    chunk_id: str
    pdf_id: str
    pdf_name: str
    page: int              # 1-based
    page_width: float
    page_height: float
    line_start: int
    line_end: int
    text: str
    bbox: dict[str, float]           # union box {x0, top, x1, bottom}
    lines: list[dict[str, float]] = field(default_factory=list)  # per-line boxes

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _page_lines(page) -> list[LineBox]:
    """Group a page's words into visual lines with bounding boxes."""
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return []
    words.sort(key=lambda w: (round(float(w["top"]) / LINE_Y_TOLERANCE), float(w["x0"])))
    lines: list[LineBox] = []
    cur: list[dict] = []
    cur_top: float | None = None
    for w in words:
        top = float(w["top"])
        if cur_top is None or abs(top - cur_top) <= LINE_Y_TOLERANCE:
            cur.append(w)
            cur_top = top if cur_top is None else cur_top
        else:
            lines.append(_line_from_words(cur))
            cur = [w]
            cur_top = top
    if cur:
        lines.append(_line_from_words(cur))
    return lines


def _line_from_words(ws: list[dict]) -> LineBox:
    ws = sorted(ws, key=lambda w: float(w["x0"]))
    return LineBox(
        text=" ".join(str(w["text"]) for w in ws).strip(),
        x0=min(float(w["x0"]) for w in ws),
        top=min(float(w["top"]) for w in ws),
        x1=max(float(w["x1"]) for w in ws),
        bottom=max(float(w["bottom"]) for w in ws),
    )


def _union(lines: list[LineBox]) -> dict[str, float]:
    return {
        "x0": min(l.x0 for l in lines),
        "top": min(l.top for l in lines),
        "x1": max(l.x1 for l in lines),
        "bottom": max(l.bottom for l in lines),
    }


def extract_chunks(pdf_id: str, pdf_name: str, pdf_path: str) -> list[Chunk]:
    """Extract chunks (with line boxes) from a PDF file.

    Opens by path (not bytes) so large files are read lazily rather than held in
    memory. Two-pass: use the embedded text layer where present; for scanned/image
    pages (no extractable text) fall back to OCR so scanned PDFs are searchable too.
    """
    import pdfplumber

    lines_by_page: dict[int, list[LineBox]] = {}   # 0-based page index -> lines
    page_meta: dict[int, dict] = {}                # 0-based -> {width, height}
    need_ocr: list[int] = []

    with pdfplumber.open(pdf_path) as pdf:
        page_dims: list[tuple[float, float]] = []
        for i, page in enumerate(pdf.pages):
            w, h = float(page.width), float(page.height)
            page_dims.append((w, h))
            page_meta[i] = {"width": w, "height": h}
            try:
                # Normalize pdfplumber's absolute points to page fractions (0..1)
                # so boxes align to the rendered page regardless of point-space.
                lines = [
                    LineBox(l.text, l.x0 / w, l.top / h, l.x1 / w, l.bottom / h)
                    for l in _page_lines(page)
                    if l.text
                ]
            except Exception:  # noqa: BLE001 - never let one bad page kill ingestion
                logger.warning("Line extraction failed on %s p%d", pdf_name, i + 1, exc_info=True)
                lines = []
            if lines:
                lines_by_page[i] = lines
            else:
                need_ocr.append(i)

    if need_ocr:
        from app.pdfchat.ocr import ocr_document

        logger.info("%s: OCR needed for %d page(s)", pdf_name, len(need_ocr))
        lines_by_page.update(ocr_document(pdf_path, need_ocr, page_dims))

    chunks: list[Chunk] = []
    for i in sorted(lines_by_page):
        pno = i + 1
        lines = lines_by_page[i]
        pw = page_meta[i]["width"]
        ph = page_meta[i]["height"]
        j = 0
        while j < len(lines):
            group: list[LineBox] = []
            chars = 0
            start = j
            while j < len(lines) and len(group) < MAX_CHUNK_LINES and (
                chars + len(lines[j].text) <= MAX_CHUNK_CHARS or not group
            ):
                group.append(lines[j])
                chars += len(lines[j].text) + 1
                j += 1
            text = " ".join(l.text for l in group).strip()
            if not text:
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"{pdf_id}:{pno}:{start}",
                    pdf_id=pdf_id,
                    pdf_name=pdf_name,
                    page=pno,
                    page_width=pw,
                    page_height=ph,
                    line_start=start,
                    line_end=j - 1,
                    text=text,
                    bbox=_union(group),
                    lines=[asdict(l) for l in group],
                )
            )
    return chunks
