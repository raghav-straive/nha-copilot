// Compute per-column totals for a result set.
//
// Only sums columns that are genuine additive measures. Columns whose name looks
// like an identifier, code, year, flag, or a rate/percentage/average are NOT
// summed (a sum of averages or of LGD codes is meaningless) — those return null.

// Matched against the column name split into words, NOT as substrings of the
// whole name. A substring regex was wrong in both directions:
//
//   suppressed real totals — "ratio" hides inside regist-RATIO-ns and
//     ope-RATIO-ns; "lat" hides inside cumu-LAT-ive, popu-LAT-ion,
//     re-LAT-ed and trans-LAT-ions; "share" matched scan_and_share.
//     Registration and Scan & Share are headline metrics of this tool, and
//     their totals silently vanished.
//
//   summed things it should not — \bavg\b and \brate\b never fired on
//     avg_amount or success_rate, because in JavaScript \b does not break at
//     an underscore (it counts as a word character). So rates and averages
//     were being added up, which produces a meaningless number.
//
// "share" is deliberately absent: in this dataset it almost always means Scan
// & Share, which is a count. A genuine proportion is caught by pct/percent.
const NON_SUMMABLE_WORDS = new Set([
  "id", "ids", "cd", "code", "codes", "pincode", "zipcode",
  "flag", "flags", "year", "yr",
  "avg", "average", "mean", "median",
  "rate", "ratio", "percent", "percentage", "pct",
  "lat", "latitude", "lon", "lng", "longitude",
]);

/** True when a column is a label or a derived proportion rather than an
 * additive measure, so a column total would be meaningless. */
function isNonSummable(column: string): boolean {
  const words = column.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  if (words.some((w) => NON_SUMMABLE_WORDS.has(w))) return true;
  // A trailing "%" survives the split above as an empty token.
  return column.includes("%");
}

function toNum(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "" && !isNaN(Number(v))) return Number(v);
  return null;
}

/** Returns a total per column (number) or null when the column isn't summable. */
export function columnTotals(
  columns: string[],
  rows: Record<string, unknown>[]
): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const c of columns) {
    if (isNonSummable(c)) {
      out[c] = null;
      continue;
    }
    let sum = 0;
    let hasNum = false;
    let ok = true;
    for (const r of rows) {
      const v = r[c];
      if (v === null || v === undefined || v === "") continue;
      const n = toNum(v);
      if (n === null) {
        ok = false;
        break;
      }
      sum += n;
      hasNum = true;
    }
    out[c] = ok && hasNum ? sum : null;
  }
  return out;
}

/** Format a number for display: whole numbers plain, decimals capped to 2 dp,
 * with thousands separators. Used for table cells, tooltips, and totals so no
 * raw float like 22.366906474… ever reaches the UI. */
export function fmtNum(n: number): string {
  return Number.isInteger(n)
    ? n.toLocaleString()
    : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** Format a total for display (alias of fmtNum, kept for call sites). */
export const formatTotal = fmtNum;
