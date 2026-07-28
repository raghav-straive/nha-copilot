"""Render a PDF page to a PNG with pypdfium2 — the SAME renderer used for OCR.

Serving the page as an image (rather than letting the browser's pdf.js re-render
the PDF) guarantees the highlight overlay aligns exactly: the displayed pixels are
the same pixels the OCR boxes were measured against. Fractions (0..1) then map
perfectly regardless of any point-space disagreement between PDF libraries.
"""
from __future__ import annotations

import functools
import io

from app.pdfchat.source import get_pdf_source

DISPLAY_DPI = 150  # crisp enough to read; boxes are fractional so DPI is free to choose


@functools.lru_cache(maxsize=64)
def render_page_png(pdf_id: str, page: int, dpi: int = DISPLAY_DPI) -> bytes:
    """Render a 1-based page to PNG bytes. Cached (last 64 pages)."""
    import pypdfium2 as pdfium

    path = get_pdf_source().get_local_path(pdf_id)
    doc = pdfium.PdfDocument(path)
    try:
        image = doc[page - 1].render(scale=dpi / 72.0).to_pil()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()
