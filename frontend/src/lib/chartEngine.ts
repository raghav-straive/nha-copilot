// Pure chart-decision engine — no React, no recharts. Given a chart spec and the
// result rows, it decides: chart type, which types are offered, how the data is
// shaped (pivot / aggregate / top-N + "Other"), axis/label formatting, and
// horizontal vs vertical layout. ChartView is a thin renderer over this; the
// engine is unit-tested in chartEngine.test.ts so the visual decisions are
// provably correct across representative queries.

export type ChartType = "bar" | "line" | "area" | "pie";

export interface ChartSpecLike {
  type?: string | null;
  x: string;
  series: string[];
  title?: string;
  drilldown?: string;
}
export type Row = Record<string, unknown>;
export interface PlotSeries {
  key: string;
  name: string;
}

// ---- Readability caps (single source of truth) ----
export const MAX_SERIES = 6; // grouped series beyond this = unreadable -> don't pivot
export const MAX_CATS_BAR = 14; // categories in a bar chart; rest folds into "Other"
export const MAX_CATS_PIE = 6; // slices in a pie; rest folds into "Other"
export const H_BAR_THRESHOLD = 8; // switch to horizontal bars beyond this many categories
export const MAX_LABELS = 16; // direct value labels only up to this many bars
export const LONG_LABEL = 10; // a category label longer than this counts as "long"
export const OTHER = "Other";

// ---- Value → friendly label maps (ABDM domain) ----
// Per-column coded-value maps. Keyed by lowercased column name.
const COLUMN_MAPS: Record<string, Record<string, string>> = {
  hpr_type: { d: "Doctor", n: "Nurse", p: "Pharmacist", D: "Doctor", N: "Nurse", P: "Pharmacist" },
  active: { t: "Active", f: "Inactive", true: "Active", false: "Inactive" },
  // facility_ownership / partner_ownership are coded G/P/PP (unlike `ownership`
  // and facility_ownership_desc, which are already full text).
  facility_ownership: { G: "Government", P: "Private", PP: "Public-Private" },
  partner_ownership: { G: "Government", P: "Private", PP: "Public-Private" },
};
// Heuristic value-set detection for coded columns whose name we don't recognise.
const VALUE_SETS: { sig: string[]; keys: string[]; map: Record<string, string> }[] = [
  { sig: ["d", "n", "p"], keys: ["d", "n", "p"], map: { d: "Doctor", n: "Nurse", p: "Pharmacist" } },
  { sig: ["t", "f"], keys: ["t", "f"], map: { t: "Active", f: "Inactive" } },
];

export function makeLabeler(col: string, values: unknown[]): (v: unknown) => string {
  const cl = (col || "").toLowerCase();
  if (COLUMN_MAPS[cl]) { const m = COLUMN_MAPS[cl]; return (v) => (v === OTHER ? OTHER : m[String(v)] ?? String(v ?? "—")); }
  const set = new Set(values.filter((v) => v != null && v !== "").map(String));
  for (const vs of VALUE_SETS) {
    if (set.size > 0 && [...set].every((x) => vs.keys.includes(x)) && vs.sig.some((k) => set.has(k))) {
      const m = vs.map; return (v) => (v === OTHER ? OTHER : m[String(v)] ?? String(v ?? "—"));
    }
  }
  return (v) => (v === OTHER ? OTHER : String(v ?? "—"));
}

export const pretty = (s: string) => {
  const t = String(s).replace(/_/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

export function compact(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e7) return (n / 1e7).toFixed(1).replace(/\.0$/, "") + "Cr";
  if (a >= 1e5) return (n / 1e5).toFixed(1).replace(/\.0$/, "") + "L";
  if (a >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
  if (Number.isInteger(n)) return String(n);
  // Non-integer under 1000 (averages, ratios): keep it short — 2 dp below 1, else 1 dp.
  return n.toFixed(a < 1 ? 2 : 1);
}

export function toNum(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "" && !isNaN(Number(v))) return Number(v);
  return null;
}

export const timeLike = (c: string) =>
  /(^|_)(date|month|year|day|week|quarter|period|time|dt|admission|dob)/i.test(c || "");

// ---- Structural facts, independent of UI toggles ----
export interface ChartAnalysis {
  cols: string[];
  numericCols: string[];
  groupKey: string | null;
  valueKey: string;
  groupDistinct: number;
  isPivot: boolean;
  tooManyGroups: boolean;
  xIsTime: boolean;
  scalesComparable: boolean;
  defaultMeasure: string; // "all" or a series name
  labelFor: (col: string, v: unknown) => string;
  fl: (v: unknown) => string;
  gl: (v: unknown) => string;
}

export function analyze(spec: ChartSpecLike, rows: Row[], columns?: string[]): ChartAnalysis {
  const cols = columns && columns.length ? columns : [spec.x, ...spec.series];

  const numericCols = cols.filter(
    (c) =>
      rows.length > 0 &&
      rows.every((r) => { const v = r[c]; return v == null || v === "" || toNum(v) !== null; }) &&
      rows.some((r) => toNum(r[c]) !== null)
  );

  const groupKey = cols.find((c) => c !== spec.x && !numericCols.includes(c)) || null;
  const valueKey =
    spec.series.find((s) => numericCols.includes(s)) ||
    numericCols.find((c) => c !== spec.x) ||
    spec.series[0];
  const groupDistinct = groupKey ? new Set(rows.map((r) => String(r[groupKey] ?? "—"))).size : 0;
  const isPivot =
    !!groupKey && !!valueKey && numericCols.includes(valueKey) && groupDistinct >= 2 && groupDistinct <= MAX_SERIES;
  const tooManyGroups = !!groupKey && !!valueKey && groupDistinct > MAX_SERIES;

  const labelers: Record<string, (v: unknown) => string> = {};
  for (const c of cols) if (!numericCols.includes(c)) labelers[c] = makeLabeler(c, rows.map((r) => r[c]));
  const labelFor = (col: string, v: unknown) =>
    labelers[col] ? labelers[col](v) : v == null || v === "" ? "—" : String(v);
  const fl = (v: unknown) => labelFor(spec.x, v);
  const gl = (v: unknown) => (groupKey ? labelFor(groupKey, v) : String(v));

  let scalesComparable = true;
  if (!groupKey && spec.series.length >= 2) {
    const maxes = spec.series.map((s) => Math.max(0, ...rows.map((r) => Math.abs(toNum(r[s]) || 0))));
    const hi = Math.max(...maxes);
    const positive = maxes.filter((m) => m > 0);
    const lo = positive.length ? Math.min(...positive) : 0;
    scalesComparable = lo > 0 && hi / lo <= 25;
  }
  const defaultMeasure = spec.series.length < 2 || scalesComparable ? "all" : spec.series[0];

  return {
    cols, numericCols, groupKey, valueKey, groupDistinct, isPivot, tooManyGroups,
    xIsTime: timeLike(spec.x), scalesComparable, defaultMeasure, labelFor, fl, gl,
  };
}

export function activeSeries(spec: ChartSpecLike, a: ChartAnalysis, measure: string): string[] {
  if (a.isPivot) return [a.valueKey];
  if (a.groupKey) return [a.valueKey];
  return measure === "all" ? spec.series : [measure];
}

// ---- Shape the plotted data (pivot / aggregate / top-N + Other) ----
export interface ChartData {
  chartData: Row[];
  plotSeries: PlotSeries[];
  folded: number;
}

export function buildChartData(spec: ChartSpecLike, rows: Row[], a: ChartAnalysis, measure: string): ChartData {
  const capBar = MAX_CATS_BAR;
  if (a.isPivot && a.groupKey && a.valueKey) {
    const groupKey = a.groupKey, valueKey = a.valueKey;
    const groups = [...new Set(rows.map((r) => String(r[groupKey] ?? "—")))].slice(0, MAX_SERIES);
    const byCat = new Map<string, Record<string, number>>();
    for (const r of rows) {
      const cat = String(r[spec.x] ?? "—"); const g = String(r[groupKey] ?? "—");
      if (!byCat.has(cat)) byCat.set(cat, {});
      byCat.get(cat)![g] = (byCat.get(cat)![g] || 0) + (toNum(r[valueKey]) || 0);
    }
    let out = [...byCat.entries()].map(([cat, gv]) => {
      const o: Row = { [spec.x]: cat };
      let tot = 0; for (const g of groups) { o[g] = gv[g] || 0; tot += (gv[g] || 0); }
      (o as any).__t = tot; return o;
    });
    if (!a.xIsTime) out.sort((x, y) => ((y as any).__t as number) - ((x as any).__t as number));
    // Fold the overflow into "Other" exactly as the single-series path below
    // does. This used to `slice(0, capBar)`, which DROPPED the extra
    // categories outright — and ChartView suppressed the "rest grouped as
    // Other" note for pivots, so a grouped chart over more than capBar
    // categories quietly showed a subset and looked complete.
    let folded = 0;
    if (out.length > capBar) {
      const head = out.slice(0, capBar - 1);
      const tail = out.slice(capBar - 1);
      folded = tail.length;
      const other: Row = { [spec.x]: OTHER };
      for (const g of groups) other[g] = tail.reduce((s, r) => s + (Number(r[g]) || 0), 0);
      out = [...head, other];
    }
    return {
      chartData: stripSortKey(out),
      plotSeries: groups.map((g) => ({ key: g, name: a.gl(g) })),
      folded,
    };
  }

  const keys = activeSeries(spec, a, measure);
  const agg = new Map<string, Record<string, number>>();
  for (const r of rows) {
    const cat = String(r[spec.x] ?? "—");
    if (!agg.has(cat)) agg.set(cat, {});
    for (const k of keys) agg.get(cat)![k] = (agg.get(cat)![k] || 0) + (toNum(r[k]) || 0);
  }
  let out = [...agg.entries()].map(([cat, kv]) => {
    const o: Row = { [spec.x]: cat };
    let tot = 0; for (const k of keys) { o[k] = kv[k] || 0; tot += (kv[k] || 0); }
    (o as any).__t = tot; return o;
  });
  if (!a.xIsTime) out.sort((x, y) => ((y as any).__t as number) - ((x as any).__t as number));
  let foldedCount = 0;
  if (out.length > capBar) {
    const head = out.slice(0, capBar - 1);
    const tail = out.slice(capBar - 1);
    foldedCount = tail.length;
    const other: Row = { [spec.x]: OTHER };
    for (const k of keys) other[k] = tail.reduce((s, r) => s + (Number(r[k]) || 0), 0);
    out = [...head, other];
  }
  return {
    chartData: stripSortKey(out),
    plotSeries: keys.map((s) => ({ key: s, name: pretty(s) })),
    folded: foldedCount,
  };
}

/** Drop the internal row total used for ordering, so it cannot reach a chart
 * tooltip, an export, or anything else that walks the row's keys. */
function stripSortKey(rows: Row[]): Row[] {
  return rows.map((r) => {
    const { __t, ...rest } = r as Row & { __t?: number };
    void __t;
    return rest as Row;
  });
}

// ---- Which chart types are offered, and the default ----
export function allowedTypes(a: ChartAnalysis, multi: boolean, catCount: number): ChartType[] {
  const allowed: ChartType[] = ["bar"];
  if (a.xIsTime) allowed.push("line");
  // Pie only for a small single-series part-to-whole — never for a time axis.
  if (!a.xIsTime && !multi && catCount <= MAX_CATS_PIE + 1) allowed.push("pie");
  return allowed;
}

export function defaultType(spec: ChartSpecLike, a: ChartAnalysis, allowed: ChartType[]): ChartType {
  const wanted = (spec.type === "area" ? "line" : spec.type) as ChartType;
  return allowed.includes(wanted) ? wanted : a.xIsTime ? "line" : "bar";
}

// ---- Layout decisions for the chosen type ----
export interface Layout {
  horizontal: boolean;
  chartHeight: number;
  showLabels: boolean;
}

export function layout(
  spec: ChartSpecLike, a: ChartAnalysis, chartData: Row[], chartType: ChartType, multi: boolean
): Layout {
  const catCount = chartData.length;
  const longLabels = chartData.some((d) => a.fl(d[spec.x]).length > LONG_LABEL);
  const horizontal = chartType === "bar" && !multi && (catCount > H_BAR_THRESHOLD || longLabels);
  const chartHeight = horizontal ? Math.max(240, catCount * 26 + 40) : 300;
  const showLabels = !multi && catCount <= MAX_LABELS;
  return { horizontal, chartHeight, showLabels };
}

// ---- One-shot resolution (used by ChartView and by the tests) ----
export interface ChartResolution {
  analysis: ChartAnalysis;
  chartData: Row[];
  plotSeries: PlotSeries[];
  folded: number;
  multi: boolean;
  catCount: number;
  allowed: ChartType[];
  chartType: ChartType;
  layout: Layout;
}

export function resolveChart(
  spec: ChartSpecLike, rows: Row[], columns?: string[],
  opts?: { chartType?: ChartType; measure?: string }
): ChartResolution {
  const analysis = analyze(spec, rows, columns);
  const measure = opts?.measure ?? analysis.defaultMeasure;
  const { chartData, plotSeries, folded } = buildChartData(spec, rows, analysis, measure);
  const multi = plotSeries.length > 1;
  const catCount = chartData.length;
  const allowed = allowedTypes(analysis, multi, catCount);
  const requested = opts?.chartType ?? defaultType(spec, analysis, allowed);
  const chartType = allowed.includes(requested) ? requested : allowed[0];
  return { analysis, chartData, plotSeries, folded, multi, catCount, allowed, chartType, layout: layout(spec, analysis, chartData, chartType, multi) };
}
