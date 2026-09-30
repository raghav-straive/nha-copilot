import { useMemo } from "react";
import { makeLabeler, toNum } from "../lib/chartEngine";
import { exportToExcel } from "../lib/exportExcel";
import { columnTotals, formatTotal, fmtNum } from "../lib/totals";

export default function ResultTable({
  columns,
  rows,
  query,
}: {
  columns: string[];
  rows: Record<string, unknown>[];
  query?: string;
}) {
  // Memoised: totals scan every row (up to the 5,000-row cap), and this
  // component re-renders whenever a new chat message arrives — so without this
  // every past result table recomputed its totals on each new answer.
  const totals = useMemo(() => columnTotals(columns, rows), [columns, rows]);

  // Decode the dataset's coded values for display — G/P/PP, t/f, d/n/p — using
  // the same mapping the charts use. The table previously showed the raw codes
  // while the chart beside it showed "Government"/"Private", and the two Excel
  // buttons produced different files from identical data.
  const cellLabel = useMemo(() => {
    const labelers: Record<string, (v: unknown) => string> = {};
    for (const c of columns) {
      const values = rows.map((r) => r[c]);
      const numeric =
        values.length > 0 &&
        values.every((v) => v == null || v === "" || toNum(v) !== null) &&
        values.some((v) => toNum(v) !== null);
      if (!numeric) labelers[c] = makeLabeler(c, values);
    }
    return (col: string, v: unknown) =>
      typeof v === "number"
        ? fmtNum(v)
        : labelers[col]
          ? labelers[col](v)
          : formatCell(v);
  }, [columns, rows]);

  if (!rows || rows.length === 0) return null;
  const showTotals = rows.length > 1 && Object.values(totals).some((v) => v !== null);
  return (
    <div className="mt-3">
      <div className="mb-1.5 flex justify-end">
        <button
          onClick={() =>
            exportToExcel({ title: "Result", columns, rows, query, labelFor: cellLabel })
          }
          className="inline-flex items-center gap-1 rounded border border-line px-2 py-1 text-[11px] font-medium text-ink-muted transition hover:border-brand hover:text-brand"
          title="Download as formatted Excel"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
          </svg>
          Excel
        </button>
      </div>
      <div className="overflow-x-auto rounded border border-line">
      <table className="min-w-full border-collapse text-[13px]">
        <thead>
          <tr className="bg-surface-alt">
            {columns.map((c) => (
              <th
                key={c}
                className="whitespace-nowrap border-b border-line px-3 py-2 text-left font-semibold text-ink-muted"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 100).map((row, i) => (
            <tr key={i} className="border-b border-line/60 last:border-0 hover:bg-brand-light/40">
              {columns.map((c) => (
                <td key={c} className="whitespace-nowrap px-3 py-1.5 tabular-nums text-ink">
                  {cellLabel(c, row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {showTotals && (
          <tfoot>
            <tr className="border-t-2 border-line-strong bg-surface-alt font-semibold">
              {columns.map((c, i) => (
                <td key={c} className="whitespace-nowrap px-3 py-1.5 tabular-nums text-ink">
                  {totals[c] !== null ? formatTotal(totals[c] as number) : i === 0 ? "Total" : ""}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
        {rows.length > 100 && (
          <div className="bg-surface-alt px-3 py-1 text-[11px] text-ink-faint">
            Showing first 100 of {rows.length} rows. (Total covers all {rows.length}.)
          </div>
        )}
      </div>
    </div>
  );
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return fmtNum(v);
  return String(v);
}
