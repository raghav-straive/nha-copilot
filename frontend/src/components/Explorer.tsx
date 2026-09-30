import { lazy, Suspense, useEffect, useState } from "react";
import { fetchExplorer, type ExplorerCard, type ExplorerData } from "../api";
// Lazy: keeps recharts out of the initial bundle. No chart exists until an
// answer arrives, so nothing is delayed by deferring it.
const ChartView = lazy(() => import("./ChartView"));
import ResultTable from "./ResultTable";

export default function Explorer({
  token,
  onExplore,
}: {
  token: string;
  onExplore: (question: string) => void;
}) {
  const [data, setData] = useState<ExplorerData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load(force = false) {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchExplorer(token, force));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-5 py-5">
      <div className="mb-4 flex items-end justify-between">
        <div>
          <h2 className="text-lg font-semibold text-ink">Explorer</h2>
          <p className="text-[13px] text-ink-muted">
            Interesting patterns and trends surfaced from the data â€” click any card to dig in.
          </p>
        </div>
        <button
          onClick={() => load(true)}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-full border border-line-strong bg-surface px-3 py-1.5 text-[12px] font-medium text-ink-muted transition hover:border-brand hover:text-brand disabled:opacity-60"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 2v6h-6M3 12a9 9 0 0 1 15-6.7L21 8M3 22v-6h6M21 12a9 9 0 0 1-15 6.7L3 16" />
          </svg>
          Refresh
        </button>
      </div>

      {loading && (
        <div className="rounded-lg border border-line bg-surface p-8 text-center shadow-soft">
          <div className="mx-auto mb-3 inline-flex gap-1">
            <span className="dot h-2 w-2 rounded-full bg-brand" />
            <span className="dot h-2 w-2 rounded-full bg-brand" />
            <span className="dot h-2 w-2 rounded-full bg-brand" />
          </div>
          <p className="shimmer-text text-sm font-medium">Discovering trends in your data…</p>
          <p className="mt-1 text-[11px] text-ink-faint">This takes a few seconds the first time.</p>
        </div>
      )}

      {error && !loading && (
        <div className="rounded-lg border border-danger-border bg-danger-bg p-4 text-sm text-danger">
          {error}
        </div>
      )}

      {!loading && data && data.insights.length === 0 && (
        <div className="rounded-lg border border-line bg-surface p-6 text-center text-sm text-ink-muted">
          No insights generated. Try Refresh, or check the backend logs.
        </div>
      )}

      {!loading && data && data.insights.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          {data.insights.map((card, i) => (
            <Card key={i} card={card} onExplore={onExplore} />
          ))}
        </div>
      )}
    </div>
  );
}

function Card({ card, onExplore }: { card: ExplorerCard; onExplore: (q: string) => void }) {
  return (
    <div className="flex flex-col rounded-lg border border-line bg-surface p-4 shadow-soft transition hover:shadow-pop">
      <h3 className="text-sm font-semibold text-ink">{card.title}</h3>
      {card.why && <p className="mt-0.5 text-[12px] text-ink-faint">{card.why}</p>}
      {card.summary && <p className="mt-2 text-[13px] text-ink">{card.summary}</p>}

      {card.chart && card.rows.length > 1 ? (
        <Suspense
          fallback={
            <div className="my-3 h-64 animate-pulse rounded border border-line bg-surface-alt" />
          }
        >
          <ChartView spec={card.chart} rows={card.rows} columns={card.columns} />
        </Suspense>
      ) : (
        card.rows.length > 0 && <ResultTable columns={card.columns} rows={card.rows} />
      )}

      {card.insights && card.insights.length > 0 && (
        <ul className="mt-2 space-y-1">
          {card.insights.slice(0, 3).map((it, i) => (
            <li key={i} className="flex gap-2 text-[12px] text-ink-muted">
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-brand" />
              <span>{it}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-auto pt-3">
        <button
          onClick={() => onExplore(card.question)}
          className="flex items-center gap-1 text-[12px] font-semibold text-brand transition hover:text-brand-dark"
        >
          Discuss in chat
          <span>â†’</span>
        </button>
      </div>
    </div>
  );
}

