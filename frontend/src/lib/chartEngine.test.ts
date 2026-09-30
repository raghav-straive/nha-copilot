import { describe, it, expect } from "vitest";
import {
  resolveChart, analyze, buildChartData, allowedTypes, defaultType,
  compact, toNum, timeLike, makeLabeler, pretty,
  MAX_CATS_BAR, MAX_CATS_PIE, OTHER, type ChartSpecLike, type Row,
} from "./chartEngine";

const rows = (...r: Row[]) => r;

// ---------------------------------------------------------------------------
// Representative queries — the "core" cases the tool must always get right.
// ---------------------------------------------------------------------------

describe("single-measure ranking (facilities by state)", () => {
  const spec: ChartSpecLike = { type: "bar", x: "state", series: ["facilities"], drilldown: "district" };
  const data = rows(
    { state: "ANDHRA PRADESH", facilities: 62 },
    { state: "BIHAR", facilities: 138 },
  );
  const r = resolveChart(spec, data);

  it("chooses a bar chart, offers bar+pie only (no line for non-time)", () => {
    expect(r.chartType).toBe("bar");
    expect(r.allowed).toEqual(["bar", "pie"]);
  });
  it("sorts categories by value descending", () => {
    expect(r.chartData.map((d) => d.state)).toEqual(["BIHAR", "ANDHRA PRADESH"]);
    expect(r.chartData.map((d) => d.facilities)).toEqual([138, 62]);
  });
  it("keeps values exact (no axis/domain padding in the data)", () => {
    const maxVal = Math.max(...r.chartData.map((d) => d.facilities as number));
    expect(maxVal).toBe(138); // never 149.04… — the engine never scales values
  });
  it("goes horizontal because a label is long (ANDHRA PRADESH)", () => {
    expect(r.layout.horizontal).toBe(true);
    expect(r.layout.showLabels).toBe(true);
  });
});

describe("small categorical breakdown (facility link active flag)", () => {
  const spec: ChartSpecLike = { type: "bar", x: "active", series: ["facilities"] };
  const data = rows(
    { active: "t", facilities: 2900 },
    { active: "f", facilities: 48 },
  );
  const r = resolveChart(spec, data);

  it("stays a vertical bar (few short labels) with value labels", () => {
    expect(r.chartType).toBe("bar");
    expect(r.layout.horizontal).toBe(false);
    expect(r.layout.showLabels).toBe(true);
  });
  it("humanizes coded values on the axis", () => {
    expect(r.analysis.fl("t")).toBe("Active");
    expect(r.analysis.fl("f")).toBe("Inactive");
  });
});

describe("time series (monthly amount paid)", () => {
  const spec: ChartSpecLike = { type: "line", x: "month", series: ["amount_paid"] };
  const data = rows(
    { month: "2025-04", amount_paid: 300 },
    { month: "2025-05", amount_paid: 100 },
    { month: "2025-06", amount_paid: 500 },
  );
  const r = resolveChart(spec, data);

  it("chooses a line chart and offers line (not pie)", () => {
    expect(r.chartType).toBe("line");
    expect(r.allowed).toContain("line");
    expect(r.allowed).not.toContain("pie");
  });
  it("preserves chronological order (does NOT sort by value)", () => {
    expect(r.chartData.map((d) => d.month)).toEqual(["2025-04", "2025-05", "2025-06"]);
  });
});

describe("grouped two-dimension (state × facility ownership)", () => {
  const spec: ChartSpecLike = { type: "bar", x: "state", series: ["facilities"] };
  const cols = ["state", "facility_ownership", "facilities"];
  const data = rows(
    { state: "GUJARAT", facility_ownership: "Government", facilities: 40 },
    { state: "GUJARAT", facility_ownership: "Private", facilities: 60 },
    { state: "BIHAR", facility_ownership: "Government", facilities: 30 },
    { state: "BIHAR", facility_ownership: "Private", facilities: 20 },
  );
  const r = resolveChart(spec, data, cols);

  it("pivots into a small number of readable series", () => {
    expect(r.analysis.isPivot).toBe(true);
    expect(r.multi).toBe(true);
    expect(r.plotSeries.map((p) => p.name)).toEqual(["Government", "Private"]);
  });
  it("offers only bar for a grouped chart (no pie, no direct labels)", () => {
    expect(r.allowed).toEqual(["bar"]);
    expect(r.layout.showLabels).toBe(false);
    expect(r.layout.horizontal).toBe(false);
  });
  it("aggregates each cell correctly", () => {
    const guj = r.chartData.find((d) => d.state === "GUJARAT")!;
    expect(guj.Government).toBe(40);
    expect(guj.Private).toBe(60);
  });
});

describe("part-to-whole (case status → pie)", () => {
  const spec: ChartSpecLike = { type: "pie", x: "case_status", series: ["cnt"] };
  const data = rows(
    { case_status: "Paid", cnt: 70 },
    { case_status: "Pending", cnt: 20 },
    { case_status: "Rejected", cnt: 10 },
  );
  const r = resolveChart(spec, data);
  it("honours the pie request for a small part-to-whole set", () => {
    expect(r.chartType).toBe("pie");
    expect(r.allowed).toContain("pie");
  });
});

describe("pie requested but too many slices → falls back to bar", () => {
  const spec: ChartSpecLike = { type: "pie", x: "district", series: ["cnt"] };
  const data = Array.from({ length: 10 }, (_, i) => ({ district: `D${i}`, cnt: i + 1 }));
  const r = resolveChart(spec, data);
  it("does not offer pie for >7 categories and falls back", () => {
    expect(r.allowed).not.toContain("pie");
    expect(r.chartType).toBe("bar");
  });
});

describe("top-N with tail folded into Other (high-cardinality single dim)", () => {
  const spec: ChartSpecLike = { type: "bar", x: "district", series: ["cnt"] };
  const data = Array.from({ length: 20 }, (_, i) => ({ district: `D${i + 1}`, cnt: i + 1 }));
  const r = resolveChart(spec, data);

  it("caps at MAX_CATS_BAR and reports how many were folded", () => {
    expect(r.catCount).toBe(MAX_CATS_BAR);
    expect(r.folded).toBe(20 - (MAX_CATS_BAR - 1));
  });
  it("adds a final Other bucket equal to the sum of the tail", () => {
    const last = r.chartData[r.chartData.length - 1];
    expect(last.district).toBe(OTHER);
    // tail = the 7 smallest of 1..20 = 1..7 → 28
    expect(last.cnt).toBe(28);
  });
  it("goes horizontal for many categories", () => {
    expect(r.layout.horizontal).toBe(true);
  });
});

describe("high-cardinality 2nd dimension → aggregate, don't explode", () => {
  const spec: ChartSpecLike = { type: "bar", x: "state", series: ["cnt"] };
  const cols = ["state", "district", "cnt"];
  const data: Row[] = [];
  for (const s of ["GUJARAT", "BIHAR"]) for (let d = 0; d < 8; d++) data.push({ state: s, district: `${s}-D${d}`, cnt: 10 });
  const r = resolveChart(spec, data, cols);

  it("detects too-many-groups and does not pivot", () => {
    expect(r.analysis.tooManyGroups).toBe(true);
    expect(r.analysis.isPivot).toBe(false);
  });
  it("collapses to a single-series total per primary category", () => {
    expect(r.plotSeries).toHaveLength(1);
    const guj = r.chartData.find((d) => d.state === "GUJARAT")!;
    expect(guj.cnt).toBe(80); // 8 districts × 10
  });
});

// ---------------------------------------------------------------------------
// Chart-type selection rules in isolation
// ---------------------------------------------------------------------------

describe("allowedTypes / defaultType rules", () => {
  const nonTime = analyze({ x: "state", series: ["v"] }, rows({ state: "A", v: 1 }, { state: "B", v: 2 }));
  const time = analyze({ x: "month", series: ["v"] }, rows({ month: "1", v: 1 }));

  it("single non-time small set → bar + pie", () => {
    expect(allowedTypes(nonTime, false, 3)).toEqual(["bar", "pie"]);
  });
  it("time series → bar + line, never pie", () => {
    expect(allowedTypes(time, false, 12)).toEqual(["bar", "line"]);
  });
  it("multi-series → bar only", () => {
    expect(allowedTypes(nonTime, true, 3)).toEqual(["bar"]);
  });
  it("large single set → bar only (no pie past the cap)", () => {
    expect(allowedTypes(nonTime, false, MAX_CATS_PIE + 2)).toEqual(["bar"]);
  });
  it("area collapses to line; unavailable request falls back", () => {
    expect(defaultType({ x: "month", series: ["v"], type: "area" }, time, ["bar", "line"])).toBe("line");
    expect(defaultType({ x: "state", series: ["v"], type: "pie" }, nonTime, ["bar"])).toBe("bar");
  });
});

// ---------------------------------------------------------------------------
// Multi-measure scale handling
// ---------------------------------------------------------------------------

describe("multi-measure scale handling", () => {
  it("comparable scales → default to plotting all series together", () => {
    const spec: ChartSpecLike = { x: "month", series: ["preauth", "paid"] };
    const data = rows({ month: "1", preauth: 100, paid: 90 }, { month: "2", preauth: 120, paid: 110 });
    const a = analyze(spec, data);
    expect(a.scalesComparable).toBe(true);
    expect(a.defaultMeasure).toBe("all");
    expect(resolveChart(spec, data).multi).toBe(true);
  });
  it("wildly different scales → default to a single measure (not a squashed chart)", () => {
    const spec: ChartSpecLike = { x: "month", series: ["claims", "amount"] };
    const data = rows({ month: "1", claims: 100, amount: 5_000_000 }, { month: "2", claims: 120, amount: 6_000_000 });
    const a = analyze(spec, data);
    expect(a.scalesComparable).toBe(false);
    expect(a.defaultMeasure).toBe("claims");
    expect(resolveChart(spec, data).multi).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Formatting & value humanization (labels)
// ---------------------------------------------------------------------------

describe("compact number formatting (currency in Cr/L/k)", () => {
  it("formats magnitudes without floating-point garbage", () => {
    expect(compact(138)).toBe("138");
    expect(compact(0)).toBe("0");
    expect(compact(1500)).toBe("1.5k");
    expect(compact(250000)).toBe("2.5L");
    expect(compact(14_904_000)).toBe("1.5Cr");
    expect(compact(20_000_000)).toBe("2Cr"); // trailing .0 stripped
  });
  it("rounds long decimals (averages/ratios) instead of dumping raw floats", () => {
    expect(compact(22.366906474)).toBe("22.4"); // was "22.366906474..."
    expect(compact(2.6747474747)).toBe("2.7");
    expect(compact(0.056789)).toBe("0.06"); // <1 keeps 2 dp
    expect(compact(999.9)).toBe("999.9");
  });
});

describe("value humanization by column / value-set", () => {
  it("maps HPR type codes", () => {
    const f = makeLabeler("hpr_type", ["d", "n", "p"]);
    expect(f("d")).toBe("Doctor");
    expect(f("n")).toBe("Nurse");
    expect(f("p")).toBe("Pharmacist");
  });
  it("maps the active flag and preserves Other", () => {
    const f = makeLabeler("active", ["t", "f"]);
    expect(f("t")).toBe("Active");
    expect(f("f")).toBe("Inactive");
    expect(f(OTHER)).toBe(OTHER);
  });
  it("infers doctor/nurse/pharmacist from the value set even without a known column name", () => {
    const f = makeLabeler("type_x", ["d", "n", "p"]);
    expect(f("d")).toBe("Doctor");
    expect(f("p")).toBe("Pharmacist");
  });
  it("pretty-prints snake_case column names", () => {
    expect(pretty("record_linked_count")).toBe("Record linked count");
  });
});

// ---------------------------------------------------------------------------
// Robustness / edge cases
// ---------------------------------------------------------------------------

describe("robustness", () => {
  it("treats numeric strings as numbers", () => {
    expect(toNum("138")).toBe(138);
    expect(toNum("")).toBeNull();
    expect(toNum("abc")).toBeNull();
    const a = analyze({ x: "state", series: ["cnt"] }, rows({ state: "A", cnt: "138" }, { state: "B", cnt: "62" }));
    expect(a.numericCols).toContain("cnt");
  });
  it("recognizes time-like column names", () => {
    expect(timeLike("month")).toBe(true);
    expect(timeLike("admission_dt")).toBe(true);
    expect(timeLike("state")).toBe(false);
  });
  it("handles a single-row scalar result cleanly", () => {
    const r = resolveChart({ x: "metric", series: ["value"] }, rows({ metric: "Total claims", value: 586872 }));
    expect(r.catCount).toBe(1);
    expect(r.chartType).toBe("bar");
    expect(r.layout.showLabels).toBe(true);
  });
  it("does not crash on empty rows", () => {
    const r = resolveChart({ x: "state", series: ["cnt"] }, []);
    expect(r.chartData).toEqual([]);
    expect(r.chartType).toBe("bar");
  });
  it("sums duplicate categories in a single dimension", () => {
    const { chartData } = buildChartData(
      { x: "state", series: ["cnt"] },
      rows({ state: "A", cnt: 10 }, { state: "A", cnt: 5 }, { state: "B", cnt: 3 }),
      analyze({ x: "state", series: ["cnt"] }, rows({ state: "A", cnt: 10 })),
      "all"
    );
    const a = chartData.find((d) => d.state === "A")!;
    expect(a.cnt).toBe(15);
  });
});

// ---------------------------------------------------------------------------
// Grouped (pivot) charts must not drop categories silently. The pivot path
// used to slice off everything past the bar cap, and ChartView suppressed the
// "rest grouped as Other" note for pivots — so a grouped chart over many
// categories showed a subset and looked complete.
// ---------------------------------------------------------------------------

describe("grouped chart over more categories than fit", () => {
  const spec: ChartSpecLike = { type: "bar", x: "district", series: ["facilities"] };
  // 20 districts × 2 ownership groups, descending totals.
  const data: Row[] = [];
  for (let i = 0; i < 20; i++) {
    data.push({ district: `D${String(i).padStart(2, "0")}`, ownership: "G", facilities: 100 - i });
    data.push({ district: `D${String(i).padStart(2, "0")}`, ownership: "P", facilities: 50 - i });
  }
  const columns = ["district", "ownership", "facilities"];
  const a = analyze(spec, data, columns);
  const { chartData, plotSeries, folded } = buildChartData(spec, data, a, a.defaultMeasure);

  it("is treated as a pivot with one series per group", () => {
    expect(a.isPivot).toBe(true);
    expect(plotSeries.map((p) => p.key).sort()).toEqual(["G", "P"]);
  });

  it("caps the number of categories plotted", () => {
    expect(chartData.length).toBe(MAX_CATS_BAR);
  });

  it("reports how many categories were folded, not the raw row count", () => {
    // 20 categories, cap of MAX_CATS_BAR with the last slot used by "Other".
    expect(folded).toBe(20 - (MAX_CATS_BAR - 1));
    expect(folded).not.toBe(data.length);
  });

  it("keeps the overflow in an Other bucket instead of discarding it", () => {
    const other = chartData.find((d) => d[spec.x] === OTHER);
    expect(other).toBeDefined();
    expect(Number(other!.G)).toBeGreaterThan(0);
    expect(Number(other!.P)).toBeGreaterThan(0);
  });

  it("preserves the grand total across every group", () => {
    for (const g of ["G", "P"]) {
      const plotted = chartData.reduce((s, d) => s + (Number(d[g]) || 0), 0);
      const expected = data
        .filter((r) => r.ownership === g)
        .reduce((s, r) => s + Number(r.facilities), 0);
      expect(plotted).toBe(expected);
    }
  });

  it("does not leak the internal sort key into the plotted rows", () => {
    for (const d of chartData) expect(Object.keys(d)).not.toContain("__t");
  });
});

describe("grouped chart that fits", () => {
  it("folds nothing and adds no Other bucket", () => {
    const spec: ChartSpecLike = { type: "bar", x: "state", series: ["n"] };
    const data = rows(
      { state: "A", grp: "G", n: 1 }, { state: "A", grp: "P", n: 2 },
      { state: "B", grp: "G", n: 3 }, { state: "B", grp: "P", n: 4 },
    );
    const a = analyze(spec, data, ["state", "grp", "n"]);
    const { chartData, folded } = buildChartData(spec, data, a, a.defaultMeasure);
    expect(folded).toBe(0);
    expect(chartData.find((d) => d.state === OTHER)).toBeUndefined();
    expect(chartData.length).toBe(2);
  });
});

describe("single-series chart does not leak the sort key either", () => {
  it("strips __t", () => {
    const { chartData } = buildChartData(
      { x: "state", series: ["cnt"] },
      rows({ state: "A", cnt: 10 }, { state: "B", cnt: 3 }),
      analyze({ x: "state", series: ["cnt"] }, rows({ state: "A", cnt: 10 })),
      "all"
    );
    for (const d of chartData) expect(Object.keys(d)).not.toContain("__t");
  });
});
