import { describe, expect, it } from "vitest";
import { columnTotals, fmtNum } from "./totals";

const rows = [{ v: 10 }, { v: 20 }, { v: 30 }];

/** Total for a single column named `name`, given numeric values. */
function totalFor(name: string) {
  const data = rows.map((r) => ({ [name]: r.v }));
  return columnTotals([name], data)[name];
}

describe("columnTotals — additive measures are summed", () => {
  // These are the headline metrics of this app. Each one must produce a total.
  it.each([
    "facilities",
    "abha_created",
    "overall_count",
    "records_linked",
    "record_linked_count",
    "transactions",
    "professionals",
    "active_links",
    "payment_amount",
    "facility_count",
    "counts",
    "n",
    // Aliases an LLM plausibly writes for the same numbers:
    "registrations",
    "registration_count",
    "total_registrations",
    "cumulative_total",
    "scan_share",
    "scan_and_share",
    "scan_share_transactions",
    "population",
    "related_facilities",
    "operations",
    "translations_count",
  ])("sums %s", (col) => {
    expect(totalFor(col)).toBe(60);
  });
});

describe("columnTotals — non-additive columns are not summed", () => {
  // Summing these is meaningless: codes and ids are labels, and a sum of
  // averages or percentages is not a number anyone wants.
  it.each([
    "id",
    "hfr_id",
    "state_code",
    "district_code",
    "pincode",
    "active_flag",
    "year",
    "financial_year",
    "avg_amount",
    "average_amount",
    "mean_value",
    "median_value",
    "success_rate",
    "paid_rate",
    "ratio",
    "percent_verified",
    "pct_active",
    "share_pct",
    "latitude",
    "longitude",
    "lat",
    "lon",
  ])("refuses to sum %s", (col) => {
    expect(totalFor(col)).toBeNull();
  });
});

describe("columnTotals — value handling", () => {
  it("ignores blanks and nulls but still totals the rest", () => {
    const data = [{ n: 10 }, { n: null }, { n: "" }, { n: 20 }, { n: undefined }];
    expect(columnTotals(["n"], data).n).toBe(30);
  });

  it("parses numeric strings", () => {
    expect(columnTotals(["n"], [{ n: "10" }, { n: "20.5" }]).n).toBe(30.5);
  });

  it("returns null when any value is non-numeric", () => {
    // A mixed column is a label column, not a measure.
    expect(columnTotals(["n"], [{ n: 10 }, { n: "Bihar" }]).n).toBeNull();
  });

  it("returns null for an all-empty column rather than zero", () => {
    // Zero would read as a real total of nothing.
    expect(columnTotals(["n"], [{ n: null }, { n: "" }]).n).toBeNull();
  });

  it("returns null for no rows", () => {
    expect(columnTotals(["n"], []).n).toBeNull();
  });

  it("ignores non-finite numbers", () => {
    expect(columnTotals(["n"], [{ n: Infinity }, { n: 5 }]).n).toBeNull();
  });
});

describe("fmtNum", () => {
  it("leaves whole numbers whole", () => {
    expect(fmtNum(1234)).toBe((1234).toLocaleString());
  });

  it("caps decimals at two places", () => {
    // No raw float like 22.366906474… should ever reach the UI.
    expect(fmtNum(22.366906474)).toBe(
      (22.37).toLocaleString(undefined, { maximumFractionDigits: 2 })
    );
  });

  it("handles zero and negatives", () => {
    expect(fmtNum(0)).toBe("0");
    expect(fmtNum(-5)).toBe((-5).toLocaleString());
  });
});
