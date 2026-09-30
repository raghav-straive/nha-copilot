import { lazy, Suspense, useState } from "react";
import type { ChatResponse } from "../api";
import Avatar from "./Avatar";
// Lazy: keeps recharts out of the initial bundle. No chart exists until an
// answer arrives, so nothing is delayed by deferring it.
const ChartView = lazy(() => import("./ChartView"));
import ResultTable from "./ResultTable";
import SqlViewer from "./SqlViewer";

export interface ChatMessage {
  sender: "user" | "assistant";
  text: string;
  data?: ChatResponse;
}

export default function MessageBubble({
  msg,
  index,
  query,
  onDrill,
  onQuickReply,
  isEditing = false,
  editingText = "",
  onStartEdit,
  onEditChange,
  onSubmitEdit,
  onCancelEdit,
}: {
  msg: ChatMessage;
  index: number;
  query?: string;
  onDrill?: (value: string, dimension: string) => void;
  onQuickReply?: (text: string) => void;
  isEditing?: boolean;
  editingText?: string;
  onStartEdit?: (index: number) => void;
  onEditChange?: (text: string) => void;
  onSubmitEdit?: () => void;
  onCancelEdit?: () => void;
}) {
  const isUser = msg.sender === "user";
  const data = msg.data;

  if (isUser) {
    // Inline edit mode
    if (isEditing) {
      return (
        <div className="flex justify-end animate-in">
          <div className="w-full max-w-[80%]">
            <textarea
              autoFocus
              rows={2}
              value={editingText}
              onChange={(e) => onEditChange?.(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  onSubmitEdit?.();
                } else if (e.key === "Escape") {
                  onCancelEdit?.();
                }
              }}
              className="w-full resize-none rounded-lg border border-brand bg-surface px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-brand/20"
            />
            <div className="mt-1 flex justify-end gap-2">
              <button
                onClick={onCancelEdit}
                className="rounded-full border border-line px-3 py-1 text-[12px] text-ink-muted hover:bg-surface-alt"
              >
                Cancel
              </button>
              <button
                onClick={onSubmitEdit}
                disabled={!editingText.trim()}
                className="rounded-full bg-brand px-3 py-1 text-[12px] font-semibold text-white hover:bg-brand-dark disabled:opacity-50"
              >
                Send
              </button>
            </div>
          </div>
        </div>
      );
    }
    // Normal user bubble with hover "edit" affordance
    return (
      <div className="group flex items-center justify-end gap-1.5 animate-in">
        {onStartEdit && (
          <button
            onClick={() => onStartEdit(index)}
            title="Edit & resend"
            aria-label="Edit message"
            className="opacity-0 transition group-hover:opacity-100 text-ink-faint hover:text-brand"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 20h9" />
              <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
            </svg>
          </button>
        )}
        <div className="max-w-[80%] whitespace-pre-wrap rounded-lg rounded-br-sm bg-brand px-4 py-2 text-sm text-white shadow-soft">
          {msg.text}
        </div>
      </div>
    );
  }

  const action = data?.action;
  const tone =
    action === "error"
      ? "border-danger-border bg-danger-bg"
      : action === "out_of_scope" || action === "clarify"
      ? "border-gold-border bg-gold-bg"
      : "border-line bg-surface";

  const badge =
    action === "error"
      ? { label: "Could not answer", cls: "bg-danger/10 text-danger" }
      : action === "clarify"
      ? { label: "Needs clarification", cls: "bg-gold/10 text-gold" }
      : action === "out_of_scope"
      ? { label: "Out of scope", cls: "bg-gold/10 text-gold" }
      : null;

  // Example questions to offer for a "chat" reply â€” from options or question texts.
  const chatExamples =
    action === "chat"
      ? (data?.options && data.options.length
          ? data.options
          : (data?.questions || []).map((q) => q.question)
        ).filter(Boolean).slice(0, 4)
      : [];

  return (
    <div className="flex justify-start gap-2 animate-in">
      <Avatar />
      <div className={`w-full max-w-[85%] rounded-lg rounded-tl-sm border px-4 py-3 shadow-soft ${tone}`}>
        {badge && (
          <span className={`mb-1.5 inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${badge.cls}`}>
            {badge.label}
          </span>
        )}
        <div className="whitespace-pre-wrap text-sm text-ink">{msg.text}</div>
        {data?.analysis && (
          <AnalysisBlock analysis={data.analysis} />
        )}
        {action === "clarify" && onQuickReply && (
          data?.questions && data.questions.length > 0 ? (
            <ClarifyForm questions={data.questions} onSubmit={onQuickReply} />
          ) : data?.options && data.options.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {data.options.map((opt) => (
                <button
                  key={opt}
                  onClick={() => onQuickReply(opt)}
                  className="rounded-full border border-brand/40 bg-brand-light px-3 py-1 text-[12px] font-medium text-brand-dark transition hover:bg-brand hover:text-white"
                >
                  {opt}
                </button>
              ))}
            </div>
          ) : null
        )}
        {action === "chat" && onQuickReply && chatExamples.length > 0 && (
          <div className="mt-2.5">
            <div className="mb-1 text-[11px] font-medium text-ink-faint">Try asking:</div>
            <div className="flex flex-wrap gap-1.5">
              {chatExamples.map((ex) => (
                <button
                  key={ex}
                  onClick={() => onQuickReply(ex)}
                  className="rounded-full border border-brand/40 bg-brand-light px-3 py-1 text-left text-[12px] font-medium text-brand-dark transition hover:bg-brand hover:text-white"
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}
        {data?.chart && data.rows && data.rows.length > 1 ? (
          <Suspense
            fallback={
              <div className="my-3 h-64 animate-pulse rounded border border-line bg-surface-alt" />
            }
          >
            <ChartView spec={data.chart} rows={data.rows} columns={data.columns} query={query} onDrill={onDrill} />
          </Suspense>
        ) : (
          data?.columns && data.rows && data.rows.length > 0 && (
            <ResultTable columns={data.columns} rows={data.rows} query={query} />
          )
        )}
        {data?.sql && <SqlViewer sql={data.sql} />}
      </div>
    </div>
  );
}

// Structured analysis: key insights + trends grounded in the fetched data.
function AnalysisBlock({
  analysis,
}: {
  analysis: { summary?: string; insights?: string[]; trends?: string[] };
}) {
  const insights = analysis.insights ?? [];
  const trends = analysis.trends ?? [];
  if (insights.length === 0 && trends.length === 0) return null;
  return (
    <div className="mt-2.5 space-y-2.5 rounded-lg border border-line bg-surface-alt/50 p-3">
      {insights.length > 0 && (
        <div>
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-brand-dark">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1h6c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2Z" />
            </svg>
            Key insights
          </div>
          <ul className="space-y-1">
            {insights.map((it, i) => (
              <li key={i} className="flex gap-2 text-[13px] text-ink">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
                <span>{it}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {trends.length > 0 && (
        <div>
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-brand-dark">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 17l6-6 4 4 8-8M21 7v5M21 7h-5" />
            </svg>
            Trends
          </div>
          <ul className="space-y-1">
            {trends.map((it, i) => (
              <li key={i} className="flex gap-2 text-[13px] text-ink">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-gold" />
                <span>{it}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// A small "form" for a multi-question clarification: one option group per question
// plus an optional free-text detail, submitted together so the model gets
// everything it needs in one round.
function ClarifyForm({
  questions,
  onSubmit,
}: {
  questions: { question: string; options: string[] }[];
  onSubmit: (text: string) => void;
}) {
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [extra, setExtra] = useState("");

  const allAnswered = questions.every((_, i) => answers[i]);
  const canSubmit = allAnswered || extra.trim().length > 0;

  function submit() {
    const parts = questions
      .map((q, i) => (answers[i] ? `${q.question} ${answers[i]}` : null))
      .filter(Boolean) as string[];
    if (extra.trim()) parts.push(extra.trim());
    const text = parts.join("; ");
    if (text) onSubmit(text);
  }

  return (
    <div className="mt-2 space-y-3">
      {questions.map((q, qi) => (
        <div key={qi}>
          <div className="text-[12px] font-medium text-ink">{q.question}</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {q.options.map((opt) => {
              const selected = answers[qi] === opt;
              return (
                <button
                  key={opt}
                  onClick={() => setAnswers((a) => ({ ...a, [qi]: opt }))}
                  className={`rounded-full px-3 py-1 text-[12px] font-medium transition ${
                    selected
                      ? "bg-brand text-white"
                      : "border border-brand/40 bg-brand-light text-brand-dark hover:bg-brand hover:text-white"
                  }`}
                >
                  {opt}
                </button>
              );
            })}
          </div>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <input
          value={extra}
          onChange={(e) => setExtra(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && canSubmit) submit();
          }}
          placeholder="Add any other detail (optional)…"
          className="flex-1 rounded-full border border-line-strong bg-surface px-3 py-1.5 text-[12px] outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
        />
        <button
          onClick={submit}
          disabled={!canSubmit}
          className="shrink-0 rounded-full bg-brand px-4 py-1.5 text-[12px] font-semibold text-white transition hover:bg-brand-dark disabled:opacity-50"
        >
          Get answer
        </button>
      </div>
    </div>
  );
}

