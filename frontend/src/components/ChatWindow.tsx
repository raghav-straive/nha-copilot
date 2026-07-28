import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { sendMessage } from "../api";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";
import Avatar from "./Avatar";
import Explorer from "./Explorer";
import MessageBubble, { type ChatMessage } from "./MessageBubble";
import WeeklyReport from "./WeeklyReport";

// Lazy — keeps react-pdf / pdfjs out of the main bundle until the tab is opened.
const PdfChat = lazy(() => import("./PdfChat"));

const SUGGESTIONS = [
  "How many facilities are registered by ownership type?",
  "Scan & Share transactions by state",
  "Top bridges by active facility links",
  "Bihar mein kitne ABHA banaye gaye?",
];

const ROLE_LABEL: Record<string, string> = {
  viewer: "Viewer",
  analyst: "Analyst",
  senior_analyst: "Senior Analyst",
  admin: "Admin",
};

export default function ChatWindow({
  token,
  role,
  username,
  onLogout,
}: {
  token: string;
  role: string;
  username: string;
  onLogout: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editingText, setEditingText] = useState("");
  const [tab, setTab] = useState<"chat" | "explorer" | "pdfs">("chat");
  const endRef = useRef<HTMLDivElement>(null);
  // Speech-to-text still needs one language hint; auto-pick from the browser
  // locale (Hindi locale -> hi-IN, otherwise Indian English which also handles
  // Hinglish). The text model detects language on its own regardless.
  const voiceLang =
    typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("hi")
      ? "hi-IN"
      : "en-IN";
  const { supported: voiceSupported, listening, start, stop } = useSpeechRecognition(
    voiceLang,
    setInput
  );

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  function startEdit(i: number) {
    setEditingIndex(i);
    setEditingText(messages[i].text);
  }
  function cancelEdit() {
    setEditingIndex(null);
    setEditingText("");
  }
  function submitEdit() {
    if (editingIndex === null || !editingText.trim() || busy) return;
    const i = editingIndex;
    const text = editingText;
    // Drop the edited message and everything after it, then resend.
    setMessages((m) => m.slice(0, i));
    setEditingIndex(null);
    setEditingText("");
    submit(text);
  }

  async function submit(text: string) {
    if (!text.trim() || busy) return;
    setMessages((m) => [...m, { sender: "user", text }]);
    setInput("");
    setBusy(true);
    try {
      const res = await sendMessage(token, text, sessionId);
      setSessionId(res.session_id);
      const body = res.answer ?? res.message ?? "(no response)";
      setMessages((m) => [...m, { sender: "assistant", text: body, data: res }]);
    } catch (err) {
      setMessages((m) => [
        ...m,
        { sender: "assistant", text: `Error: ${(err as Error).message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-line bg-surface px-5 py-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded bg-brand text-[11px] font-bold text-white">
            NHA
          </div>
          <div>
            <h1 className="text-sm font-semibold leading-tight text-ink">
              NHA Analytics Co-Pilot
            </h1>
            <p className="text-[11px] leading-tight text-ink-faint">
              ABDM digital-adoption analytics
            </p>
          </div>
        </div>
        <div className="flex overflow-hidden rounded-full border border-line text-[13px]">
          {([
            { key: "chat", label: "Chat with Data" },
            { key: "explorer", label: "Data Explorer" },
            { key: "pdfs", label: "Chat with PDFs" },
          ] as const).map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-1.5 font-medium transition ${
                tab === t.key ? "bg-brand text-white" : "bg-surface text-ink-muted hover:bg-brand-light"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <WeeklyReport token={token} />
          <span className="hidden rounded-full border border-line bg-surface-alt px-2.5 py-0.5 text-[11px] font-medium text-ink-muted sm:inline">
            {username} · {ROLE_LABEL[role] ?? role}
          </span>
          <button
            onClick={onLogout}
            className="text-[12px] font-medium text-brand transition hover:text-brand-dark"
          >
            Sign out
          </button>
        </div>
      </header>

      {tab === "pdfs" ? (
        <div className="min-h-0 flex-1">
          <Suspense fallback={<div className="p-6 text-sm text-ink-faint">Loading…</div>}>
            <PdfChat token={token} />
          </Suspense>
        </div>
      ) : tab === "explorer" ? (
        <div className="flex-1 overflow-y-auto">
          <Explorer
            token={token}
            onExplore={(q) => {
              setTab("chat");
              submit(q);
            }}
          />
        </div>
      ) : (
        <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col overflow-hidden">
      {/* Messages */}
      <main className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
        {messages.length === 0 && (
          <div className="mx-auto max-w-lg pt-8">
            <div className="rounded-lg border border-line bg-surface p-5 shadow-soft">
              <h2 className="text-sm font-semibold text-ink">Ask a question</h2>
              <p className="mt-1 text-[13px] text-ink-muted">
                Plain English works — the co-pilot picks the right ABDM data
                (registrations, ABHA, linking, or transactions) and shows the SQL it used.
              </p>
              <div className="mt-4 grid gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => submit(s)}
                    className="group flex items-center justify-between rounded border border-line bg-surface px-3 py-2 text-left text-[13px] text-ink transition-all duration-150 hover:-translate-y-0.5 hover:border-brand hover:bg-brand-light/40 hover:shadow-soft active:translate-y-0"
                  >
                    <span>{s}</span>
                    <span className="text-ink-faint transition-transform duration-150 group-hover:translate-x-0.5 group-hover:text-brand">
                      →
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <MessageBubble
            key={i}
            index={i}
            msg={m}
            query={messages[i - 1]?.text}
            isEditing={editingIndex === i}
            editingText={editingText}
            onStartEdit={busy ? undefined : startEdit}
            onEditChange={setEditingText}
            onSubmitEdit={submitEdit}
            onCancelEdit={cancelEdit}
            onQuickReply={(text) => submit(text)}
            onDrill={(value, dimension) => {
              // Compose a self-contained drill query using the question that
              // produced this chart (the preceding user message).
              const origin = messages[i - 1]?.text ?? "";
              submit(`${origin} — specifically for ${value}, broken down by ${dimension}`.trim());
            }}
          />
        ))}
        {busy && (
          <div className="flex justify-start gap-2 animate-in">
            <Avatar live />
            <div className="flex items-center gap-2.5 rounded-lg rounded-tl-sm border border-line bg-surface px-4 py-3 shadow-soft">
              <span className="inline-flex items-center gap-1">
                <span className="dot h-1.5 w-1.5 rounded-full bg-brand" />
                <span className="dot h-1.5 w-1.5 rounded-full bg-brand" />
                <span className="dot h-1.5 w-1.5 rounded-full bg-brand" />
              </span>
              <span className="shimmer-text text-[12px] font-medium">Analyzing your question…</span>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </main>

      {/* Composer */}
      <footer className="border-t border-line bg-surface px-5 py-3">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (listening) stop();
            submit(input);
          }}
          className="flex items-center gap-2"
        >
          {voiceSupported && (
            <button
              type="button"
              onClick={() => (listening ? stop() : start())}
              title={listening ? "Stop listening" : "Speak your question (English, हिंदी, or Hinglish)"}
              aria-label="Voice input"
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full border transition ${
                listening
                  ? "border-danger bg-danger text-white pulse-glow"
                  : "border-line-strong bg-surface text-ink-muted hover:border-brand hover:text-brand"
              }`}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="2" width="6" height="12" rx="3" />
                <path d="M5 10a7 7 0 0 0 14 0M12 17v4" />
              </svg>
            </button>
          )}
          <input
            className="flex-1 rounded-full border border-line-strong bg-surface px-4 py-2.5 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
            placeholder={listening ? "Listening… speak now" : "Ask in English, हिंदी, or Hinglish — type or use the mic"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="rounded-full bg-brand px-5 py-2.5 text-sm font-semibold text-white shadow-soft transition-all duration-150 hover:bg-brand-dark hover:shadow-pop active:scale-95 disabled:scale-100 disabled:opacity-50"
          >
            Send
          </button>
        </form>
        <p className="mt-1.5 text-center text-[10px] text-ink-faint">
          Answers are generated from ABDM adoption data (prototype). Verify the SQL before acting on results.
        </p>
      </footer>
        </div>
      )}
    </div>
  );
}
