import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fetchPdfPageUrl, type LineBox } from "../api";

// Image-based viewer: shows the server-rendered page PNG (the exact render the OCR
// boxes were measured against) and overlays highlights as fractions × the image's
// displayed size — so highlights line up pixel-for-pixel regardless of PDF
// point-space quirks across libraries.
export default function PdfViewer({
  token,
  pdfId,
  docName,
  targetPage,
  highlights,
  numPages,
}: {
  token: string;
  pdfId: string | null;
  docName?: string;
  targetPage: number;
  highlights: LineBox[]; // page fractions (0..1)
  numPages: number;
}) {
  const [page, setPage] = useState(targetPage || 1);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);
  const hlRef = useRef<HTMLDivElement>(null);
  const cache = useRef<Map<string, string>>(new Map());

  // Follow the cited page whenever a new citation opens.
  useEffect(() => {
    if (targetPage) setPage(targetPage);
  }, [targetPage, pdfId]);

  // Load the page image (cached per pdf:page).
  useEffect(() => {
    if (!pdfId) return;
    let cancelled = false;
    const key = `${pdfId}:${page}`;
    setDims(null);
    const cached = cache.current.get(key);
    if (cached) {
      setImgUrl(cached);
      return;
    }
    setLoading(true);
    fetchPdfPageUrl(token, pdfId, page)
      .then((u) => {
        if (cancelled) return;
        cache.current.set(key, u);
        setImgUrl(u);
      })
      .catch(() => !cancelled && setImgUrl(null))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [pdfId, page, token]);

  useEffect(() => () => cache.current.forEach((u) => URL.revokeObjectURL(u)), []);

  const measure = () => {
    if (imgRef.current && imgRef.current.clientWidth) {
      setDims({ w: imgRef.current.clientWidth, h: imgRef.current.clientHeight });
    }
  };
  useLayoutEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  // Scroll the highlight into view once the image + dims are ready.
  useEffect(() => {
    const t = setTimeout(() => hlRef.current?.scrollIntoView({ block: "center", behavior: "smooth" }), 200);
    return () => clearTimeout(t);
  }, [dims, page, highlights]);

  const onCited = page === targetPage && highlights.length > 0 && !!dims;

  if (!pdfId) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-sm text-ink-faint">
        Click a citation in an answer to open the source PDF here, at the exact page and line.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 border-b border-line bg-surface-alt px-3 py-1.5">
        <span className="truncate text-[12px] font-medium text-ink" title={docName}>{docName}</span>
        <div className="flex shrink-0 items-center gap-1.5 text-[12px]">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="rounded border border-line px-2 py-0.5 text-ink-muted disabled:opacity-40 hover:border-brand hover:text-brand"
          >‹</button>
          <span className="tabular-nums text-ink-muted">{page} / {numPages || "…"}</span>
          <button
            onClick={() => setPage((p) => Math.min(numPages || p + 1, p + 1))}
            disabled={!!numPages && page >= numPages}
            className="rounded border border-line px-2 py-0.5 text-ink-muted disabled:opacity-40 hover:border-brand hover:text-brand"
          >›</button>
        </div>
      </div>

      {/* Page image + highlight overlay */}
      <div className="flex-1 overflow-auto bg-surface-alt/60 p-3">
        <div className="relative mx-auto w-fit shadow-pop">
          {imgUrl && (
            <img
              ref={imgRef}
              src={imgUrl}
              alt={`${docName} page ${page}`}
              onLoad={measure}
              className="block max-w-full"
            />
          )}
          {loading && <div className="p-6 text-sm text-ink-faint">Rendering page…</div>}
          {onCited && dims && (
            <div className="pointer-events-none absolute inset-0">
              {highlights.map((b, i) => (
                <div
                  key={i}
                  ref={i === 0 ? hlRef : undefined}
                  className="absolute rounded-sm"
                  style={{
                    left: b.x0 * dims.w - 2,
                    top: b.top * dims.h - 1,
                    width: (b.x1 - b.x0) * dims.w + 4,
                    height: (b.bottom - b.top) * dims.h + 2,
                    background: "rgba(237, 200, 40, 0.32)",
                    boxShadow: "0 0 0 1px rgba(200, 150, 0, 0.55)",
                  }}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
