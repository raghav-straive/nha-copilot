import { useMemo, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { ChartSpec } from "../api";
import { exportToExcel } from "../lib/exportExcel";
import { exportChartToPptx } from "../lib/exportPptx";
import { columnTotals, formatTotal, fmtNum } from "../lib/totals";
import {
  analyze, buildChartData, allowedTypes, defaultType, layout,
  compact, pretty, toNum, MAX_CATS_BAR, OTHER, type ChartType,
} from "../lib/chartEngine";

const BRAND = "#0f7c8b";
const CATEGORICAL = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"];
const AXIS = "#6a7b83";
const GRID = "#e2e7ea";

type View = "chart" | "table";

function CustomTooltip({ active, payload, label, fmt }: any) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="rounded border border-line bg-surface px-3 py-2 text-[12px] shadow-pop">
      <div className="mb-1 font-semibold text-ink">{fmt(label ?? payload[0]?.name)}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey ?? p.name} className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: p.color || p.payload?.fill }} />
          <span className="text-ink-muted">{p.name}:</span>
          <span className="font-medium tabular-nums text-ink">
            {typeof p.value === "number" ? fmtNum(p.value) : String(p.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function ChartView({
  spec, rows, columns, query, onDrill,
}: {
  spec: ChartSpec;
  rows: Record<string, unknown>[];
  columns?: string[];
  query?: string;
  onDrill?: (value: string, dimension: string) => void;
}) {
  const analysis = useMemo(() => analyze(spec, rows, columns), [spec, rows, columns]);
  const { numericCols, groupKey, valueKey, groupDistinct, isPivot, tooManyGroups, xIsTime, scalesComparable, labelFor, fl } = analysis;

  const [measure, setMeasure] = useState<string>(analysis.defaultMeasure);

  const { chartData, plotSeries, folded } = useMemo(
    () => buildChartData(spec, rows, analysis, measure),
    [spec, rows, analysis, measure]
  );
  const multi = plotSeries.length > 1;
  const catCount = chartData.length;

  const allowed = useMemo(() => allowedTypes(analysis, multi, catCount), [analysis, multi, catCount]);
  const [type, setType] = useState<ChartType>(defaultType(spec, analysis, allowed));
  const chartType = allowed.includes(type) ? type : allowed[0];

  const { horizontal, chartHeight, showLabels } = layout(spec, analysis, chartData, chartType, multi);

  const [view, setView] = useState<View>("chart");

  const colorFor = (i: number) => (multi ? CATEGORICAL[i % CATEGORICAL.length] : BRAND);
  const drillable = !!(spec.drilldown && onDrill);
  const handleDrill = (p: any) => {
    if (!drillable || !p) return;
    const val = p[spec.x] ?? p.name ?? p.payload?.[spec.x];
    if (val != null && val !== OTHER) onDrill!(fl(val), spec.drilldown!);
  };

  const pieData = chartData.map((d) => ({ [spec.x]: d[spec.x], __v: plotSeries.reduce((s, ps) => s + (Number(d[ps.key]) || 0), 0) }));

  const tableColumns = isPivot || tooManyGroups ? [spec.x, groupKey!, valueKey!] : [spec.x, ...spec.series];
  const cellFmt = (col: string, val: unknown) => {
    if (val == null || val === "") return "—";
    if (numericCols.includes(col)) { const n = toNum(val); return n !== null ? fmtNum(n) : String(val); }
    return labelFor(col, val);
  };

  const exportPpt = () => exportChartToPptx({
    title: spec.title || "Result", type: chartType,
    categories: chartData.map((d) => fl(d[spec.x])),
    series: plotSeries.map((ps) => ({ name: ps.name, values: chartData.map((d) => Number(d[ps.key]) || 0) })),
    query,
  });
  const exportXlsx = () => exportToExcel({ title: spec.title || "Result", columns: tableColumns, rows, query, labelFor: (c, v) => cellFmt(c, v) });

  const xAxisProps: any = {
    dataKey: spec.x, tick: { fontSize: 11, fill: AXIS }, tickFormatter: fl,
    interval: 0, angle: 0, textAnchor: "middle", height: 30, tickMargin: 8, minTickGap: 0,
  };

  const renderSeries = (kind: "bar" | "line" | "area") =>
    plotSeries.map((ps, i) =>
      kind === "line" ? (
        <Line key={ps.key} name={ps.name} type="monotone" dataKey={ps.key} stroke={colorFor(i)} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
      ) : kind === "area" ? (
        <Area key={ps.key} name={ps.name} type="monotone" dataKey={ps.key} stroke={colorFor(i)} fill={colorFor(i)} fillOpacity={0.15} strokeWidth={2} />
      ) : (
        <Bar key={ps.key} name={ps.name} dataKey={ps.key} fill={colorFor(i)} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={horizontal ? 22 : 64}
          onClick={(e: any) => handleDrill(e?.payload ?? e)} cursor={drillable ? "pointer" : "default"}>
          {!multi && chartData.map((_, idx) => <Cell key={idx} fill={BRAND} />)}
          {showLabels && (
            <LabelList dataKey={ps.key} position={horizontal ? "right" : "top"}
              formatter={(v: any) => compact(Number(v))} style={{ fontSize: 10, fill: AXIS }} />
          )}
        </Bar>
      )
    );

  return (
    <figure className="mt-3 rounded-lg border border-line bg-surface p-3">
      <figcaption className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[13px] font-semibold text-ink">{spec.title || "Result"}</span>
        <div className="flex items-center gap-1">
          <div className="flex overflow-hidden rounded border border-line">
            {(["chart", "table"] as View[]).map((v) => (
              <button key={v} onClick={() => setView(v)}
                className={`px-2 py-0.5 text-[11px] capitalize transition ${view === v ? "bg-ink text-white" : "bg-surface text-ink-muted hover:bg-surface-alt"}`}>{v}</button>
            ))}
          </div>
          {view === "chart" && allowed.length > 1 && (
            <div className="ml-1 flex overflow-hidden rounded border border-line">
              {allowed.map((t) => (
                <button key={t} onClick={() => setType(t)}
                  className={`px-2 py-0.5 text-[11px] capitalize transition ${chartType === t ? "bg-brand text-white" : "bg-surface text-ink-muted hover:bg-brand-light"}`}>{t}</button>
              ))}
            </div>
          )}
          <button onClick={exportPpt} title="Editable PowerPoint slide" className="rounded border border-line px-2 py-0.5 text-[11px] font-medium text-ink-muted transition hover:border-brand hover:text-brand">PPT</button>
          <button onClick={exportXlsx} title="Data as Excel" className="rounded border border-line px-2 py-0.5 text-[11px] font-medium text-ink-muted transition hover:border-brand hover:text-brand">Excel</button>
        </div>
      </figcaption>

      {!groupKey && spec.series.length > 1 && view === "chart" && chartType !== "pie" && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-ink-faint">Measure:</span>
          {scalesComparable && <MeasureBtn active={measure === "all"} onClick={() => setMeasure("all")}>All</MeasureBtn>}
          {spec.series.map((s) => <MeasureBtn key={s} active={measure === s} onClick={() => setMeasure(s)}>{pretty(s)}</MeasureBtn>)}
        </div>
      )}

      {view === "table" ? (
        <MiniTable columns={tableColumns} rows={rows} fmt={cellFmt} />
      ) : (
        <ResponsiveContainer width="100%" height={chartHeight}>
          {chartType === "pie" ? (
            <PieChart>
              <Tooltip content={(p: any) => <CustomTooltip {...p} fmt={fl} />} />
              <Legend wrapperStyle={{ fontSize: 12 }} formatter={(v: any) => fl(v)} />
              <Pie data={pieData} dataKey="__v" nameKey={spec.x} cx="50%" cy="50%" outerRadius={95} innerRadius={45} paddingAngle={2}
                onClick={(e: any) => handleDrill(e?.payload ?? e)} cursor={drillable ? "pointer" : "default"}>
                {pieData.map((_, i) => <Cell key={i} fill={CATEGORICAL[i % CATEGORICAL.length]} stroke="#fff" strokeWidth={2} />)}
              </Pie>
            </PieChart>
          ) : chartType === "line" ? (
            <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis {...xAxisProps} /><YAxis tick={{ fontSize: 11, fill: AXIS }} tickFormatter={compact} width={46} />
              <Tooltip content={(p: any) => <CustomTooltip {...p} fmt={fl} />} />
              {multi && <Legend wrapperStyle={{ fontSize: 12 }} />}
              {renderSeries("line")}
            </LineChart>
          ) : horizontal ? (
            <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: showLabels ? 52 : 24, bottom: 4, left: 8 }} barCategoryGap="20%">
              <CartesianGrid stroke={GRID} horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11, fill: AXIS }} tickFormatter={compact} />
              <YAxis type="category" dataKey={spec.x} tick={{ fontSize: 11, fill: AXIS }} tickFormatter={fl} width={130} interval={0} />
              <Tooltip content={(p: any) => <CustomTooltip {...p} fmt={fl} />} cursor={{ fill: "rgba(15,124,139,0.06)" }} />
              {multi && <Legend wrapperStyle={{ fontSize: 12 }} />}
              {renderSeries("bar")}
            </BarChart>
          ) : (
            <BarChart data={chartData} margin={{ top: showLabels ? 26 : 10, right: 16, bottom: 4, left: 4 }} barCategoryGap="22%">
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis {...xAxisProps} />
              <YAxis tick={{ fontSize: 11, fill: AXIS }} tickFormatter={compact} width={46} allowDecimals={false} />
              <Tooltip content={(p: any) => <CustomTooltip {...p} fmt={fl} />} cursor={{ fill: "rgba(15,124,139,0.06)" }} />
              {multi && <Legend wrapperStyle={{ fontSize: 12 }} />}
              {renderSeries("bar")}
            </BarChart>
          )}
        </ResponsiveContainer>
      )}

      <div className="mt-1 space-y-0.5 text-center">
        {isPivot && chartType !== "pie" && <p className="text-[11px] text-ink-faint">Grouped by {pretty(groupKey!)}.</p>}
        {tooManyGroups && <p className="text-[11px] text-ink-faint">{pretty(groupKey!)} has {groupDistinct} values — showing totals by {pretty(spec.x)}; use Table for the full breakdown.</p>}
        {/* Shown for grouped charts too: the pivot path now folds its overflow
            into "Other" instead of dropping it, so the note is accurate there
            and a grouped chart no longer omits categories in silence. */}
        {folded > 0 && <p className="text-[11px] text-ink-faint">Top {MAX_CATS_BAR - 1} shown; the rest grouped as “Other”. Use Table for all.</p>}
        {drillable && view === "chart" && <p className="text-[11px] text-ink-faint">Tip: click a {chartType === "pie" ? "slice" : "bar"} to drill into {pretty(spec.drilldown!)}.</p>}
      </div>
    </figure>
  );
}

function MeasureBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={`rounded-full border px-2.5 py-0.5 text-[11px] transition ${active ? "border-brand bg-brand text-white" : "border-line bg-surface text-ink-muted hover:border-brand"}`}>{children}</button>
  );
}

function MiniTable({ columns, rows, fmt }: { columns: string[]; rows: Record<string, unknown>[]; fmt: (col: string, val: unknown) => string }) {
  const totals = columnTotals(columns, rows);
  const showTotals = rows.length > 1 && Object.values(totals).some((v) => v !== null);
  return (
    <div className="max-h-72 overflow-auto rounded border border-line">
      <table className="min-w-full text-[12px]">
        <thead className="sticky top-0 bg-surface-alt">
          <tr>{columns.map((c) => <th key={c} className="border-b border-line px-3 py-1.5 text-left font-semibold text-ink-muted">{pretty(c)}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 200).map((r, i) => (
            <tr key={i} className="border-b border-line/60 last:border-0">
              {columns.map((c) => <td key={c} className="px-3 py-1 tabular-nums text-ink">{fmt(c, r[c])}</td>)}
            </tr>
          ))}
        </tbody>
        {showTotals && (
          <tfoot><tr className="border-t-2 border-line-strong bg-surface-alt font-semibold">
            {columns.map((c, i) => <td key={c} className="px-3 py-1 tabular-nums text-ink">{totals[c] !== null ? formatTotal(totals[c] as number) : i === 0 ? "Total" : ""}</td>)}
          </tr></tfoot>
        )}
      </table>
    </div>
  );
}
