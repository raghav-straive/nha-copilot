"""OCR fallback for scanned / image-only PDFs (no text layer).

Renders each page with pypdfium2 (pure-pip, no system deps) and OCRs it with
Tesseract via pytesseract, grouping words into lines with bounding boxes. Boxes
are converted from render pixels back to PDF points so they line up with the
viewer's highlight overlay exactly like the text path.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from app.config import get_settings
from app.pdfchat.ingest import LineBox

logger = logging.getLogger(__name__)

_COMMON = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
    "/usr/local/bin/tesseract",
]


def tesseract_available() -> bool:
    """Configure pytesseract's binary path and report whether OCR can run."""
    import pytesseract

    cmd = get_settings().tesseract_cmd.strip()
    if cmd and Path(cmd).exists():
        pytesseract.pytesseract.tesseract_cmd = cmd
        return True
    if shutil.which("tesseract"):
        return True
    for c in _COMMON:
        if os.path.exists(c):
            pytesseract.pytesseract.tesseract_cmd = c
            return True
    return False


def ocr_document(
    pdf_path: str, page_indices: list[int], page_dims: list[tuple[float, float]], dpi: int | None = None
) -> dict[int, list[LineBox]]:
    """OCR the given 0-based page indices. Returns {page_index: [LineBox]} in page fractions."""
    if not page_indices:
        return {}
    if not tesseract_available():
        logger.warning(
            "Tesseract not found — scanned PDFs cannot be read. Install it and/or set "
            "TESSERACT_CMD. See https://github.com/UB-Mannheim/tesseract/wiki (Windows)."
        )
        return {}

    import pypdfium2 as pdfium
    import pytesseract
    from concurrent.futures import ThreadPoolExecutor
    from pytesseract import Output

    dpi = dpi or get_settings().ocr_dpi
    scale = dpi / 72.0
    out: dict[int, list[LineBox]] = {}
    workers = min(4, (os.cpu_count() or 2))

    def _ocr_image(item):
        idx, img = item
        try:
            d = pytesseract.image_to_data(img, output_type=Output.DICT)
            return idx, _lines_from_tsv(d, float(img.width), float(img.height))
        except Exception:  # noqa: BLE001 - one bad page shouldn't kill ingestion
            logger.warning("OCR failed on page %d", idx, exc_info=True)
            return idx, []

    # Process in small batches: render a handful of pages (sequential — pypdfium2
    # isn't safe for concurrent access to one doc), OCR them in parallel, then free
    # the images before the next batch. Bounds memory so a big scanned PDF (dozens
    # of high-DPI page images) never exhausts RAM.
    BATCH = workers
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        for start in range(0, len(page_indices), BATCH):
            batch = page_indices[start : start + BATCH]
            images: list[tuple[int, "object"]] = []
            for idx in batch:
                try:
                    images.append((idx, pdf[idx].render(scale=scale).to_pil()))
                except Exception:  # noqa: BLE001
                    logger.warning("Render failed on page %d", idx, exc_info=True)
                    out[idx] = []
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for idx, lines in ex.map(_ocr_image, images):
                    out[idx] = lines
            images.clear()
    finally:
        pdf.close()
    return out


def _lines_from_tsv(d: dict, img_w: float, img_h: float) -> list[LineBox]:
    """Group Tesseract word boxes (pixels) into lines, normalized to page fractions
    (0..1) so they align to the rendered page regardless of point-space quirks."""
    n = len(d["text"])
    groups: dict[tuple, dict] = {}
    for i in range(n):
        txt = (d["text"][i] or "").strip()
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not txt or conf < 30:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        g = groups.setdefault(key, {"words": [], "x0": 1e9, "top": 1e9, "x1": 0.0, "bottom": 0.0})
        g["words"].append((x, txt))
        g["x0"] = min(g["x0"], x)
        g["top"] = min(g["top"], y)
        g["x1"] = max(g["x1"], x + w)
        g["bottom"] = max(g["bottom"], y + h)

    lines: list[LineBox] = []
    for key in sorted(groups):
        g = groups[key]
        text = " ".join(t for _, t in sorted(g["words"], key=lambda p: p[0])).strip()
        if not text:
            continue
        lines.append(
            LineBox(
                text=text,
                x0=g["x0"] / img_w,
                top=g["top"] / img_h,
                x1=g["x1"] / img_w,
                bottom=g["bottom"] / img_h,
            )
        )
    return lines
