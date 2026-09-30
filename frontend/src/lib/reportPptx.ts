import type { WeeklyReport } from "../api";

// Editable PowerPoint weekly ABDM report in the app's design language.

const BRAND = "0F7C8B";
const BRAND_DARK = "0A5B66";
const BRAND_LIGHT = "E8F3F5";
const INK = "233139";
const MUTED = "6A7B83";
const GREEN = "2F9E44";
const RED = "A52C2C";
const CATEGORICAL = ["2A78D6", "1BAF7A", "EDA100", "008300", "4A3AA7", "E34948", "E87BA4", "EB6834"];
const STATUS_COLORS: Record<string, string> = {
  SUCCESS: "2F9E44", PENDING: "B08400", CANCELED: "E06A2C", FAIL: "A52C2C",
  ACTIVE: "2F9E44", DEACTIVATE: "A52C2C", CONSUMER: "2A78D6",
};
const FONT = "Trebuchet MS";

const fmtDate = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
function periodLabel(start: string, end: string) {
  const last = new Date(end + "T00:00:00");
  last.setDate(last.getDate() - 1);
  return `${fmtDate(start)} – ${fmtDate(last.toISOString().slice(0, 10))}`;
}
function inr(n: number) {
  const a = Math.abs(n);
  if (a >= 1e7) return "₹" + (n / 1e7).toFixed(2).replace(/\.00$/, "") + " Cr";
  if (a >= 1e5) return "₹" + (n / 1e5).toFixed(2).replace(/\.00$/, "") + " L";
  if (a >= 1e3) return "₹" + (n / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
  return "₹" + Math.round(n).toLocaleString("en-IN");
}
const num = (n: number) => Math.round(n).toLocaleString("en-IN");
const nowStr = () => new Date().toLocaleString(undefined, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
// NOTE: these coded-value mappings are also defined in chartEngine.ts
// (COLUMN_MAPS / VALUE_SETS), which the chat charts and result tables use. If
// the dataset gains a new ownership or professional code, update BOTH — they
// are deliberately separate because this deck is built without the chart
// engine, but they must agree.
const ownerName = (o: string) => (o === "G" ? "Government" : o === "P" ? "Private" : o === "PP" ? "Public-Private" : o);
const hprName = (h: string) => (h === "d" ? "Doctor" : h === "n" ? "Nurse" : h === "p" ? "Pharmacist" : h);

export async function buildWeeklyReport(report: WeeklyReport) {
  const mod: any = await import("pptxgenjs");
  const P = mod.default || mod;
  const p = new P();
  p.defineLayout({ name: "W", width: 13.333, height: 7.5 });
  p.layout = "W";
  const W = 13.333;
  const label = periodLabel(report.period.start, report.period.end);

  const footer = (s: any) =>
    s.addText(`Generated ${nowStr()}  •  NHA Analytics Co-Pilot — ABDM adoption data (prototype). Verify before use.`,
      { x: 0.5, y: 7.08, w: 12.3, h: 0.32, fontFace: FONT, fontSize: 8, italic: true, color: MUTED });
  const heading = (s: any, t: string, sub?: string) => {
    s.addText(t, { x: 0.5, y: 0.3, w: 9.5, h: 0.55, fontFace: FONT, fontSize: 21, bold: true, color: INK });
    if (sub) s.addText(sub, { x: 0.5, y: 0.82, w: 9.5, h: 0.3, fontFace: FONT, fontSize: 11, color: MUTED });
    s.addShape(p.ShapeType.rect, { x: 0.5, y: sub ? 1.14 : 0.92, w: 1.8, h: 0.045, fill: { color: BRAND } });
    s.addText(label, { x: 9.5, y: 0.35, w: 3.3, h: 0.4, align: "right", fontFace: FONT, fontSize: 11, color: BRAND_DARK });
  };
  const tile = (s: any, x: number, y: number, w: number, h: number, value: string, lbl: string, delta?: { pct: number | null; up?: boolean }) => {
    s.addShape(p.ShapeType.roundRect, { x, y, w, h, rectRadius: 0.08, fill: { color: BRAND_LIGHT }, line: { color: BRAND, width: 0.5 } });
    s.addText(value, { x: x + 0.18, y: y + 0.14, w: w - 0.36, h: 0.55, fontFace: FONT, fontSize: 23, bold: true, color: BRAND_DARK });
    s.addText(lbl, { x: x + 0.18, y: y + 0.74, w: w - 0.36, h: 0.35, fontFace: FONT, fontSize: 11, color: MUTED });
    if (delta && delta.pct !== null && delta.pct !== undefined) {
      const up = delta.up ?? delta.pct >= 0;
      s.addText(`${up ? "▲" : "▼"} ${Math.abs(delta.pct)}% vs last wk`,
        { x: x + w - 1.9, y: y + 0.16, w: 1.7, h: 0.3, align: "right", fontFace: FONT, fontSize: 9, color: up ? GREEN : RED });
    }
  };
  const bar = (s: any, o: any) =>
    s.addChart(p.ChartType.bar, [{ name: o.name || "Value", labels: o.labels, values: o.values }], {
      x: o.x, y: o.y, w: o.w, h: o.h, barDir: o.dir || "col", chartColors: [o.color || BRAND],
      showLegend: false, showTitle: !!o.title, title: o.title, titleFontFace: FONT, titleFontSize: 12, titleColor: INK,
      catAxisLabelFontFace: FONT, valAxisLabelFontFace: FONT, catAxisLabelFontSize: 9, valAxisLabelFontSize: 9, dataLabelFontFace: FONT,
    });
  const pie = (s: any, o: any) =>
    s.addChart(p.ChartType.pie, [{ name: o.name || "Value", labels: o.labels, values: o.values }], {
      x: o.x, y: o.y, w: o.w, h: o.h, showLegend: true, legendPos: "b", legendFontFace: FONT, legendFontSize: 10,
      showTitle: !!o.title, title: o.title, titleFontFace: FONT, titleFontSize: 12, titleColor: INK,
      showPercent: true, dataLabelFontFace: FONT, dataLabelColor: "FFFFFF", chartColors: o.colors || CATEGORICAL,
    });
  const table = (s: any, header: string[], rows: string[][], o: any) => {
    const head = header.map((t) => ({ text: t, options: { bold: true, color: "FFFFFF", fill: { color: BRAND }, fontFace: FONT, fontSize: 10 } }));
    const body = rows.map((r) => r.map((c) => ({ text: c, options: { color: INK, fontFace: FONT, fontSize: 10 } })));
    s.addTable([head, ...body], { x: o.x, y: o.y, w: o.w, colW: o.colW, border: { type: "solid", color: "E2E7EA", pt: 0.5 }, valign: "middle" });
  };

  const k = report.kpis;

  // ---------- 1. Title ----------
  const s1 = p.addSlide();
  s1.background = { color: BRAND };
  s1.addShape(p.ShapeType.rect, { x: 0, y: 4.4, w: W, h: 3.1, fill: { color: "FFFFFF" } });
  s1.addText("NHA", { x: 0.6, y: 0.6, w: 2, h: 0.7, fontFace: FONT, fontSize: 22, bold: true, color: "FFFFFF" });
  s1.addText("ABDM Weekly Report", { x: 0.6, y: 2.0, w: 12, h: 1, fontFace: FONT, fontSize: 40, bold: true, color: "FFFFFF" });
  s1.addText(`Week of ${label}`, { x: 0.6, y: 3.1, w: 12, h: 0.7, fontFace: FONT, fontSize: 22, color: BRAND_LIGHT });
  s1.addText("NHA Analytics Co-Pilot", { x: 0.6, y: 5.0, w: 12, h: 0.6, fontFace: FONT, fontSize: 18, bold: true, color: BRAND_DARK });
  s1.addText(`Generated ${nowStr()}`, { x: 0.6, y: 5.6, w: 12, h: 0.5, fontFace: FONT, fontSize: 12, color: MUTED });
  s1.addText("ABDM adoption data — prototype. Figures are illustrative.", { x: 0.6, y: 6.6, w: 12, h: 0.4, fontFace: FONT, fontSize: 10, italic: true, color: MUTED });

  // ---------- 2. Executive summary + headline KPIs ----------
  const s2 = p.addSlide(); s2.background = { color: "FFFFFF" };
  heading(s2, "Executive summary", "ABDM digital-adoption activity this week");
  const tiles: [string, string, any?][] = [
    [num(k.abha_created), "ABHA created", { pct: k.wow.abha_created.pct }],
    [num(k.records_linked), "Records linked", { pct: k.wow.records_linked.pct }],
    [num(k.scan_share_txns), "Scan & Share txns", { pct: k.wow.scan_share_txns.pct }],
    [num(k.scan_pay_txns), "Scan & Pay txns", { pct: k.wow.scan_pay_txns.pct }],
    [num(k.facilities_verified), "Facilities verified"],
    [num(k.hpr_verified), "Professionals verified"],
  ];
  const tw = 3.9, th = 1.2;
  tiles.forEach((t, i) => tile(s2, 0.5 + (i % 3) * (tw + 0.2), 1.35 + Math.floor(i / 3) * (th + 0.2), tw, th, t[0], t[1], t[2]));
  if (report.analysis?.summary)
    s2.addText([{ text: "Summary  ", options: { bold: true, color: BRAND_DARK } }, { text: report.analysis.summary, options: { color: INK } }],
      { x: 0.5, y: 4.2, w: 12.3, h: 1.1, fontFace: FONT, fontSize: 13, valign: "top", fill: { color: "F4F6F7" }, line: { color: "E2E7EA", width: 0.5 } });
  if (report.analysis?.insights?.length)
    s2.addText(report.analysis.insights.map((t) => ({ text: t, options: { bullet: { code: "2022" }, color: INK, breakLine: true } })),
      { x: 0.5, y: 5.45, w: 12.3, h: 1.5, fontFace: FONT, fontSize: 11, valign: "top" });
  footer(s2);

  // ---------- 3. ABHA creation & facility registration ----------
  const s3 = p.addSlide(); s3.background = { color: "FFFFFF" };
  heading(s3, "ABHA creation & facility registration", "Health IDs by state; facility mix by ownership and type");
  if (report.abha_by_state.length)
    bar(s3, { x: 0.5, y: 1.4, w: 5.6, h: 4.7, title: "ABHA created by state", labels: report.abha_by_state.slice(0, 8).map((r) => r.state || "—"), values: report.abha_by_state.slice(0, 8).map((r) => r.abha_created) });
  if (report.facilities_by_ownership.length)
    pie(s3, { x: 6.4, y: 1.4, w: 3.1, h: 4.7, title: "Facilities by ownership", labels: report.facilities_by_ownership.map((r) => ownerName(r.ownership)), values: report.facilities_by_ownership.map((r) => r.facilities) });
  if (report.facilities_by_type.length)
    table(s3, ["Facility type", "Facilities"], report.facilities_by_type.slice(0, 8).map((r) => [r.facility_type || "—", num(r.facilities)]), { x: 9.7, y: 1.5, w: 3.1, colW: [2.1, 1.0] });
  footer(s3);

  // ---------- 4. Health-record linking ----------
  const s4 = p.addSlide(); s4.background = { color: "FFFFFF" };
  heading(s4, "Health-record linking", "Clinical documents linked to patients' ABHA");
  tile(s4, 0.5, 1.4, 3.9, 1.3, num(k.records_linked), "Records linked this week", { pct: k.wow.records_linked.pct });
  tile(s4, 4.6, 1.4, 3.9, 1.3, num(k.active_facility_links), "Active facility–bridge links");
  if (report.linked_by_state.length)
    bar(s4, { x: 0.5, y: 3.0, w: 6.1, h: 3.6, color: CATEGORICAL[1], title: "Records linked by state", labels: report.linked_by_state.slice(0, 8).map((r) => r.state || "—"), values: report.linked_by_state.slice(0, 8).map((r) => r.records_linked) });
  if (report.links_by_bridge.length)
    bar(s4, { x: 6.9, y: 3.0, w: 5.9, h: 3.6, dir: "bar", color: CATEGORICAL[3], title: "Top bridges by active links", labels: report.links_by_bridge.slice(0, 8).map((r) => r.bridge_name || "—"), values: report.links_by_bridge.slice(0, 8).map((r) => r.active_links) });
  footer(s4);

  // ---------- 5. Scan & Share / Scan & Pay ----------
  const s5 = p.addSlide(); s5.background = { color: "FFFFFF" };
  heading(s5, "Transaction adoption", "Scan & Share and Scan & Pay volumes");
  tile(s5, 0.5, 1.4, 3.9, 1.3, num(k.scan_share_txns), "Scan & Share txns", { pct: k.wow.scan_share_txns.pct });
  tile(s5, 4.6, 1.4, 3.9, 1.3, num(k.scan_pay_txns), "Scan & Pay txns", { pct: k.wow.scan_pay_txns.pct });
  tile(s5, 8.7, 1.4, 3.9, 1.3, inr(k.scan_pay_amount), "Scan & Pay amount");
  if (report.scan_share_by_state.length)
    bar(s5, { x: 0.5, y: 3.0, w: 6.1, h: 3.6, title: "Scan & Share by state", labels: report.scan_share_by_state.slice(0, 8).map((r) => r.state || "—"), values: report.scan_share_by_state.slice(0, 8).map((r) => r.transactions) });
  if (report.scan_pay_by_status.length)
    pie(s5, { x: 7.0, y: 3.0, w: 5.8, h: 3.6, title: "Scan & Pay by payment status", labels: report.scan_pay_by_status.map((r) => r.payment_status), values: report.scan_pay_by_status.map((r) => r.records), colors: report.scan_pay_by_status.map((r) => STATUS_COLORS[r.payment_status] || CATEGORICAL[0]) });
  footer(s5);

  // ---------- 6. Professionals & bridges ----------
  const s6 = p.addSlide(); s6.background = { color: "FFFFFF" };
  heading(s6, "Professionals & bridges", "HPR registration mix and integrator status");
  if (report.hpr_by_type.length)
    pie(s6, { x: 0.5, y: 1.4, w: 5.6, h: 4.7, title: "Professionals by type", labels: report.hpr_by_type.map((r) => hprName(r.hpr_type)), values: report.hpr_by_type.map((r) => r.professionals) });
  if (report.bridge_by_status.length)
    pie(s6, { x: 6.5, y: 1.4, w: 3.2, h: 4.7, title: "Bridges by status", labels: report.bridge_by_status.map((r) => r.status), values: report.bridge_by_status.map((r) => r.bridges), colors: report.bridge_by_status.map((r) => STATUS_COLORS[r.status] || CATEGORICAL[0]) });
  if (report.analysis?.trends?.length)
    s6.addText([{ text: "Week-over-week\n", options: { bold: true, color: BRAND_DARK, fontSize: 13 } },
      ...report.analysis.trends.map((t) => ({ text: t, options: { bullet: { code: "2022" }, color: INK, breakLine: true, fontSize: 11 } }))],
      { x: 9.9, y: 1.5, w: 2.9, h: 4.6, fontFace: FONT, valign: "top" });
  footer(s6);

  await p.writeFile({ fileName: `ABDM-weekly-report-${report.period.start}.pptx` });
}
