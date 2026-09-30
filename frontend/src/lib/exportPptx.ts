// Export a chart as an editable PowerPoint slide (native chart object).

const BRAND = "0F7C8B";
const CATEGORICAL = ["2A78D6", "1BAF7A", "EDA100", "008300", "4A3AA7", "E34948", "E87BA4", "EB6834"];
const INK = "233139";
const MUTED = "6A7B83";
const FONT = "Trebuchet MS";

function timestamp(): string {
  const d = new Date();
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function slug(s: string): string {
  return (s || "chart").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "chart";
}

export async function exportChartToPptx(opts: {
  title: string;
  type: "bar" | "line" | "area" | "pie";
  categories: string[]; // x labels (already friendly)
  series: { name: string; values: number[] }[];
  query?: string;
  /** True when the on-screen chart is a horizontal bar chart. The engine turns
   * bars sideways once there are many categories or the labels are long; the
   * export always used vertical columns, so the slide did not match the screen
   * and long state names ended up crammed onto the category axis. */
  horizontal?: boolean;
}) {
  const { title, type, categories, series, query, horizontal = false } = opts;
  const mod: any = await import("pptxgenjs");
  const PptxGen = mod.default || mod;
  const pptx = new PptxGen();
  pptx.defineLayout({ name: "W", width: 13.333, height: 7.5 });
  pptx.layout = "W";

  const slide = pptx.addSlide();
  slide.background = { color: "FFFFFF" };

  // Title
  slide.addText(title || "Result", {
    x: 0.5, y: 0.35, w: 12.3, h: 0.7,
    fontFace: FONT, fontSize: 24, bold: true, color: INK,
  });
  // Teal accent rule under the title
  slide.addShape(pptx.ShapeType.rect, { x: 0.5, y: 1.05, w: 2.2, h: 0.05, fill: { color: BRAND } });

  const multi = series.length > 1;
  const chartType =
    type === "pie" ? pptx.ChartType.pie
    : type === "line" ? pptx.ChartType.line
    : type === "area" ? pptx.ChartType.area
    : pptx.ChartType.bar;

  const data =
    type === "pie"
      ? [{ name: series[0]?.name || "Value", labels: categories, values: series[0]?.values || [] }]
      : series.map((s) => ({ name: s.name, labels: categories, values: s.values }));

  slide.addChart(chartType, data, {
    x: 0.5, y: 1.3, w: 12.3, h: 5.2,
    chartColors: multi || type === "pie" ? CATEGORICAL : [BRAND],
    showLegend: multi || type === "pie",
    legendPos: "b",
    legendFontFace: FONT,
    legendFontSize: 10,
    showTitle: false,
    showValue: type === "pie",
    showPercent: type === "pie",
    dataLabelFontFace: FONT,
    catAxisLabelFontFace: FONT,
    catAxisLabelFontSize: 10,
    valAxisLabelFontFace: FONT,
    valAxisLabelFontSize: 10,
    barDir: horizontal ? "bar" : "col",
    ...(type === "line" || type === "area" ? { lineSmooth: true } : {}),
  });

  // Footnote
  const notes = [
    query ? `Query: ${query}` : "",
    `Generated: ${timestamp()}  •  NHA Analytics Co-Pilot (ABDM adoption data)`,
  ].filter(Boolean).join("\n");
  slide.addText(notes, {
    x: 0.5, y: 6.7, w: 12.3, h: 0.6,
    fontFace: FONT, fontSize: 9, italic: true, color: MUTED, valign: "top",
  });

  await pptx.writeFile({ fileName: `${slug(title)}.pptx` });
}
