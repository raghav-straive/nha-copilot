"""OCR fallback for scanned / image-only PDFs (no text layer).

Renders each page with pypdfium2 (pure-pip, no system deps) and reads text with
the configured OCR engine (settings.ocr_engine):

  * "tesseract" — local Tesseract binary via pytesseract (offline, no billing).
  * "vision"    — Google Cloud Vision DOCUMENT_TEXT_DETECTION (better on noisy
                  scans, nothing to install on the server; needs the Vision API
                  enabled + billing and the same service-account credentials the
                  rest of the app uses).
  * "auto"      — Vision if it's importable with credentials, else Tesseract.

Both engines emit word boxes that are grouped into lines and normalized to page
fractions (0..1), so the viewer's highlight overlay lines up exactly regardless
of which engine produced them. If the chosen engine is unavailable at runtime it
falls back gracefully (vision -> tesseract -> none) so ingestion never hard-fails.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
from pathlib import Path
from threading import Lock

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

_vision_singleton = None  # google.cloud.vision.ImageAnnotatorClient, created once
_vision_lock = Lock()     # so two concurrent first-uses don't build two clients
# Whether the Vision pre-flight probe succeeded. Cached for the process: the API
# being enabled and the credentials being authorized does not change per
# document, and the probe was costing one Vision call for every PDF indexed.
_vision_probe_result: bool | None = None


# --------------------------------------------------------------------------- #
# Engine availability & selection
# --------------------------------------------------------------------------- #
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


def _vision_available() -> bool:
    """True if the Vision SDK is importable and some Google credentials are present."""
    try:
        import google.cloud.vision  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    s = get_settings()
    if (s.google_credentials_json or "").strip() or (s.google_application_credentials or "").strip():
        return True
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return True
    try:  # ambient / Application Default Credentials (e.g. attached VM SA)
        import google.auth

        creds, _ = google.auth.default()
        return creds is not None
    except Exception:  # noqa: BLE001
        return False


def _resolve_engine() -> str | None:
    """Pick the engine to actually use, honoring settings with graceful fallback."""
    want = (get_settings().ocr_engine or "tesseract").strip().lower()
    if want in ("vision", "auto") and _vision_available():
        return "vision"
    if want == "vision":  # asked for Vision but it's not usable
        logger.warning("ocr_engine=vision but Vision is unavailable — falling back to Tesseract.")
    if tesseract_available():
        return "tesseract"
    return None


def _vision_client():
    """Lazily build (and cache) a Vision client using the app's credentials."""
    global _vision_singleton
    if _vision_singleton is not None:
        return _vision_singleton
    with _vision_lock:
        if _vision_singleton is not None:  # built while we waited
            return _vision_singleton
        import json

        from google.cloud import vision

        s = get_settings()
        inline = (s.google_credentials_json or "").strip()
        if inline:
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_info(json.loads(inline))
            _vision_singleton = vision.ImageAnnotatorClient(credentials=creds)
        elif (s.google_application_credentials or "").strip():
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_file(s.google_application_credentials)
            _vision_singleton = vision.ImageAnnotatorClient(credentials=creds)
        else:
            _vision_singleton = vision.ImageAnnotatorClient()  # ADC
    return _vision_singleton


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def ocr_document(
    pdf_path: str, page_indices: list[int], dpi: int | None = None
) -> dict[int, list[LineBox]]:
    """OCR the given 0-based page indices. Returns {page_index: [LineBox]} in page fractions.

    Boxes come back as page fractions measured against the rendered image, so
    the caller's point-space page dimensions are not needed here (an earlier
    `page_dims` argument was passed in but never used).
    """
    if not page_indices:
        return {}

    engine = _resolve_engine()
    if engine is None:
        logger.warning(
            "No OCR engine available — scanned PDFs cannot be read. Install Tesseract "
            "(set TESSERACT_CMD) or configure Google Vision (OCR_ENGINE=vision + credentials)."
        )
        return {}

    # Pre-flight for Vision: a tiny probe call confirms the API is enabled and the
    # credentials are authorized. If it's disabled/denied at runtime, fall back to
    # Tesseract so a misconfigured deployment still reads scans instead of silently
    # indexing blank pages. Costs one trivial Vision call per document.
    if engine == "vision" and not _vision_probe_ok() and tesseract_available():
        logger.warning("Vision probe failed — falling back to Tesseract for this document.")
        engine = "tesseract"

    logger.info("OCR engine: %s (%d page(s))", engine, len(page_indices))

    import pypdfium2 as pdfium
    from concurrent.futures import ThreadPoolExecutor

    dpi = dpi or get_settings().ocr_dpi
    scale = dpi / 72.0
    out: dict[int, list[LineBox]] = {}
    workers = min(4, (os.cpu_count() or 2))

    def _read_page(item):
        idx, img = item
        try:
            if engine == "vision":
                return idx, _vision_lines(_png_bytes(img))
            return idx, _tesseract_lines(img)
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
        # One pool for the whole document rather than one per batch — spinning
        # worker threads up and down for every few pages is pure overhead.
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for start in range(0, len(page_indices), BATCH):
                batch = page_indices[start : start + BATCH]
                images: list[tuple[int, "object"]] = []
                for idx in batch:
                    try:
                        images.append((idx, pdf[idx].render(scale=scale).to_pil()))
                    except Exception:  # noqa: BLE001
                        logger.warning("Render failed on page %d", idx, exc_info=True)
                        out[idx] = []
                for idx, lines in ex.map(_read_page, images):
                    out[idx] = lines
                images.clear()
    finally:
        pdf.close()
    return out


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _vision_probe_ok() -> bool:
    """Tiny call to confirm Vision is actually usable (API enabled + authorized).

    Cached for the process — whether the API is enabled and the credentials are
    authorized does not vary per document, and this was spending one Vision call
    for every PDF indexed.
    """
    global _vision_probe_result
    if _vision_probe_result is not None:
        return _vision_probe_result
    try:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
        _vision_lines(buf.getvalue())  # blank image -> no text, but exercises the API
        _vision_probe_result = True
    except Exception as e:  # noqa: BLE001
        logger.warning("Vision probe error: %s", str(e)[:200])
        _vision_probe_result = False
    return _vision_probe_result


# --------------------------------------------------------------------------- #
# Tesseract engine
# --------------------------------------------------------------------------- #
def _tesseract_lines(img) -> list[LineBox]:
    import pytesseract
    from pytesseract import Output

    d = pytesseract.image_to_data(img, output_type=Output.DICT)
    return _lines_from_tsv(d, float(img.width), float(img.height))


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


# --------------------------------------------------------------------------- #
# Google Vision engine
# --------------------------------------------------------------------------- #
def _vision_lines(content: bytes) -> list[LineBox]:
    """OCR one page image with Cloud Vision; group words into lines (page fractions)."""
    from google.cloud import vision

    client = _vision_client()
    hints = [h.strip() for h in get_settings().vision_language_hints.split(",") if h.strip()]
    ctx = vision.ImageContext(language_hints=hints) if hints else None
    resp = client.document_text_detection(image=vision.Image(content=content), image_context=ctx)
    if resp.error and resp.error.message:
        raise RuntimeError(f"Vision API error: {resp.error.message}")

    fta = resp.full_text_annotation
    if not fta or not fta.pages:
        return []
    page = fta.pages[0]
    pw = float(page.width) or 1.0
    ph = float(page.height) or 1.0

    # Flatten to word boxes (pixels), then greedily group into lines by vertical
    # overlap — robust across Vision versions without relying on break enums.
    words: list[tuple[float, float, float, float, str]] = []
    for block in page.blocks:
        for para in block.paragraphs:
            for word in para.words:
                txt = "".join(sym.text for sym in word.symbols).strip()
                verts = word.bounding_box.vertices
                if not txt or not verts:
                    continue
                xs = [v.x for v in verts]
                ys = [v.y for v in verts]
                words.append((min(ys), min(xs), max(xs), max(ys), txt))

    words.sort(key=lambda w: (w[0], w[1]))
    rows: list[dict] = []
    for y0, x0, x1, y1, txt in words:
        cy = (y0 + y1) / 2.0
        h = max(y1 - y0, 1.0)
        # Match against the row's ANCHOR centre — the centre of its first word —
        # not its accumulated vertical span. Testing against the growing span
        # let one unusually tall word (a superscript, a table rule, an inline
        # mark) stretch a row until the NEXT line's centre fell inside it,
        # merging two visual lines into one. A citation highlight then covered
        # text the answer never cited.
        #
        # The tolerance uses the SMALLER of the two word heights, so a tall word
        # joining a row cannot widen the band for everything after it. This is
        # the same anchor-plus-tolerance approach _page_lines uses in ingest.py.
        row = next(
            (r for r in rows if abs(cy - r["anchor"]) <= 0.6 * min(r["h0"], h)),
            None,
        )
        if row is None:
            rows.append({
                "words": [(x0, txt)], "x0": x0, "x1": x1, "y0": y0, "y1": y1,
                "anchor": cy, "h0": h,
            })
        else:
            row["words"].append((x0, txt))
            row["x0"], row["x1"] = min(row["x0"], x0), max(row["x1"], x1)
            row["y0"], row["y1"] = min(row["y0"], y0), max(row["y1"], y1)

    lines: list[LineBox] = []
    for r in sorted(rows, key=lambda r: r["y0"]):
        text = " ".join(t for _, t in sorted(r["words"], key=lambda p: p[0])).strip()
        if not text:
            continue
        lines.append(
            LineBox(text=text, x0=r["x0"] / pw, top=r["y0"] / ph, x1=r["x1"] / pw, bottom=r["y1"] / ph)
        )
    return lines
