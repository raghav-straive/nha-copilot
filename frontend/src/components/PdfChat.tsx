import { useEffect, useRef, useState } from "react";
import {
  fetchPdfDocuments,
  sendPdfMessage,
  type LineBox,
  type PdfCitation,
  type PdfDocument,
} from "../api";
import Avatar from "./Avatar";
import PdfViewer from "./PdfViewer";

interface PdfMsg {
  sender: "user" | "assistant";
  text: string;
  citations?: PdfCitation[];
  found?: boolean;
}

interface Active {
  pdfId: string;
  docName: string;
  page: number;
  highlights: LineBox[];
  numPages: number;
  key: number; // bumps so the viewer re-targets even to the same page
}

const SUGGESTIONS = [
  "Summarise the key points across these documents",
  "What does the document say about eligibility?",
];

export default function PdfChat({ token }: { token: string }) {
  const [messages, setMessages] = useState<PdfMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [docs, setDocs] = useState<PdfDocument[]>([]);
  const [active, setActive] = useState<Active | null>(null);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const clickSeq = useRef(0);

  useEffect(() => {
    fetchPdfDocuments(token).then(setDocs).catch(() => setDocs([]));
  }, [token]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  function openCitation(c: PdfCitation) {
    setError(null);
    try {
      const doc = docs.find((d) => d.id === c.pdf_id);
      setActive({
        pdfId: c.pdf_id,
        docName: c.pdf_name,
        page: c.page,
        highlights: c.lines || [],
        numPages: doc?.pages ?? 0,
        key: ++clickSeq.current,
      });
    } catch {
      setError("Could not open that PDF.");
    }
  }

  async function submit(q?: string) {
    const text = (q ?? input).trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { sender: "user", text }]);
    setBusy(true);
    setError(null);
    try {
      const res = await sendPdfMessage(token, text);
      setMessages((m) => [...m, { sender: "assistant", text: res.answer, citations: res.citations, found: res.found }]);
      // Auto-open the first citation so the source is immediately visible.
      if (res.citations && res.citations.length > 0) openCitation(res.citations[0]);
    } catch (e) {
      setMessages((m) => [...m, { sender: "assistant", text: "Sorry, I couldn't answer that just now." }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full min-h-0">
      {/* Left: chat */}
      <div className="flex min-h-0 w-[44%] min-w-[340px] flex-col border-r border-line">
        <div className="border-b border-line bg-surface px-4 py-2">
          <div className="text-[13px] font-semibold text-ink">Chat with PDFs</div>
          <div className="text-[11px] text-ink-faint">
            {docs.length > 0
              ? `${docs.length} document${docs.length > 1 ? "s" : ""} · answers cite the exact page & line`
              : "No PDFs available yet — add PDFs to the corpus."}
          </div>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {messages.length === 0 && (
            <div className="mx-auto max-w-md pt-6">
              <div className="rounded-lg border border-line bg-surface p-4 shadow-soft">
                <h2 className="text-sm font-semibold text-ink">Ask about your documents</h2>
                <p className="mt-1 text-[13px] text-ink-muted">
                  Answers are grounded in the PDFs. Click a{" "}
                  <span className="rounded bg-brand-light px-1 font-medium text-brand-dark">[1]</span>{" "}
                  citation to open that PDF on the right at the exact page and highlighted line.
                </p>
                {docs.length > 0 && (
                  <div className="mt-3 grid gap-2">
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        onClick={() => submit(s)}
                        className="rounded-lg border border-line bg-surface-alt px-3 py-2 text-left text-[13px] text-ink transition hover:border-brand hover:bg-brand-light"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {messages.map((m, i) =>
            m.sender === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-lg rounded-br-sm bg-brand px-3.5 py-2 text-sm text-white shadow-soft">
                  {m.text}
                </div>
              </div>
            ) : (
              <div key={i} className="flex justify-start gap-2">
                <Avatar />
                <div className="w-full max-w-[88%] rounded-lg rounded-tl-sm border border-line bg-surface px-4 py-3 shadow-soft">
                  <div className="text-sm leading-relaxed text-ink">
                    {renderAnswer(m.text, m.citations || [], openCitation)}
                  </div>
                  {m.citations && m.citations.length > 0 && (
                    <div className="mt-2.5 border-t border-line/70 pt-2">
                      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Sources</div>
                      <div className="flex flex-col gap-1">
                        {m.citations.map((c) => (
                          <button
                            key={c.n}
                            onClick={() => openCitation(c)}
                            className="group flex items-start gap-2 rounded px-1.5 py-1 text-left text-[12px] transition hover:bg-brand-light"
                          >
                            <span className="mt-0.5 shrink-0 rounded bg-brand-light px-1.5 font-semibold text-brand-dark group-hover:bg-brand group-hover:text-white">
                              {c.n}
                            </span>
                            <span className="text-ink-muted">
                              <span className="font-medium text-ink">{c.pdf_name}</span> · p.{c.page}
                              <span className="ml-1 text-ink-faint">— {c.snippet}</span>
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )
          )}
          {busy && (
            <div className="flex items-center gap-2 text-[13px] text-ink-faint">
              <Avatar /> Searching the documents…
            </div>
          )}
          {error && <div className="rounded border border-danger-border bg-danger-bg px-3 py-1.5 text-[12px] text-danger">{error}</div>}
          <div ref={endRef} />
        </div>

        <div className="border-t border-line bg-surface px-3 py-3">
          <div className="flex items-end gap-2">
            <textarea
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder={docs.length ? "Ask about the documents…" : "No PDFs available"}
              disabled={!docs.length || busy}
              className="max-h-32 flex-1 resize-none rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20 disabled:opacity-60"
            />
            <button
              onClick={() => submit()}
              disabled={!input.trim() || busy}
              className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-dark disabled:opacity-50"
            >
              Send
            </button>
          </div>
        </div>
      </div>

      {/* Right: PDF viewer */}
      <div className="min-h-0 flex-1">
        <PdfViewer
          key={active?.key ?? "empty"}
          token={token}
          pdfId={active?.pdfId ?? null}
          docName={active?.docName}
          targetPage={active?.page ?? 1}
          highlights={active?.highlights ?? []}
          numPages={active?.numPages ?? 0}
        />
      </div>
    </div>
  );
}

// Render answer text with inline [n] markers turned into clickable citation chips.
function renderAnswer(text: string, citations: PdfCitation[], onCite: (c: PdfCitation) => void) {
  const parts: React.ReactNode[] = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const n = parseInt(m[1], 10);
    const c = citations.find((x) => x.n === n);
    if (c) {
      parts.push(
        <button
          key={`c${k++}`}
          onClick={() => onCite(c)}
          title={`${c.pdf_name} · p.${c.page}`}
          className="mx-0.5 inline-flex -translate-y-0.5 items-center rounded bg-brand-light px-1 text-[10px] font-bold text-brand-dark align-super transition hover:bg-brand hover:text-white"
        >
          {n}
        </button>
      );
    } else {
      parts.push(m[0]);
    }
    last = re.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}
