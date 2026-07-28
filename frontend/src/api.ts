// Typed client for the FastAPI backend.
//
// In dev, VITE_API_BASE is unset -> relative URLs go through the Vite proxy.
// In production (GitHub Pages), set VITE_API_BASE to the backend's HTTPS URL.
const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
const url = (path: string) => `${API_BASE}${path}`;

export interface LoginResponse {
  access_token: string;
  token_type: string;
  role: string;
  username: string;
}

export interface ChartSpec {
  type: "bar" | "line" | "area" | "pie" | "none";
  x: string;
  series: string[];
  title?: string;
  drilldown?: string;
}

export interface ChatResponse {
  session_id: string;
  action: "answer" | "clarify" | "out_of_scope" | "error" | "chat";
  answer?: string | null;
  message?: string | null;
  sql?: string | null;
  columns: string[];
  rows: Record<string, unknown>[];
  chart?: ChartSpec | null;
  options?: string[];
  questions?: { question: string; options: string[] }[];
  analysis?: { summary?: string; insights?: string[]; trends?: string[] } | null;
  context_chips: Record<string, string>;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const body = new URLSearchParams({ username, password });
  const res = await fetch(url("/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) throw new Error("Invalid username or password");
  return res.json();
}

export interface Delta {
  prev: number;
  change: number;
  pct: number | null;
}
export interface WeeklyReport {
  period: { start: string; end: string };
  kpis: {
    abha_created: number;
    facilities_verified: number;
    hpr_verified: number;
    records_linked: number;
    scan_share_txns: number;
    scan_pay_txns: number;
    scan_pay_amount: number;
    active_facility_links: number;
    states_covered: number;
    wow: {
      abha_created: Delta;
      records_linked: Delta;
      scan_share_txns: Delta;
      scan_pay_txns: Delta;
    };
  };
  abha_by_state: { state: string; abha_created: number }[];
  scan_share_by_state: { state: string; transactions: number }[];
  linked_by_state: { state: string; records_linked: number }[];
  facilities_by_ownership: { ownership: string; facilities: number }[];
  facilities_by_type: { facility_type: string; facilities: number }[];
  hpr_by_type: { hpr_type: string; professionals: number }[];
  scan_pay_by_status: { payment_status: string; records: number; amount: number }[];
  links_by_bridge: { bridge_name: string; active_links: number }[];
  bridge_by_status: { status: string; bridges: number }[];
  analysis: { summary?: string; insights?: string[]; trends?: string[] } | null;
}

export async function fetchWeeklyReport(
  token: string,
  start: string,
  end: string
): Promise<WeeklyReport> {
  const res = await fetch(url(`/report/weekly?start=${start}&end=${end}`), {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Report failed (${res.status}): ${await res.text()}`);
  return res.json();
}

export interface ExplorerCard {
  title: string;
  question: string;
  why?: string;
  summary?: string;
  insights?: string[];
  chart?: ChartSpec | null;
  columns: string[];
  rows: Record<string, unknown>[];
  sql?: string | null;
}
export interface ExplorerData {
  generated_at: string;
  insights: ExplorerCard[];
}

export async function fetchExplorer(token: string, force = false): Promise<ExplorerData> {
  const res = await fetch(url(`/explorer?force=${force}`), {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Explorer failed (${res.status}): ${await res.text()}`);
  return res.json();
}

// ---- Chat with PDFs ----
export interface LineBox {
  text: string;
  x0: number;
  top: number;
  x1: number;
  bottom: number;
}
export interface PdfCitation {
  n: number;
  pdf_id: string;
  pdf_name: string;
  page: number; // 1-based
  page_width: number; // PDF points
  page_height: number;
  bbox: { x0: number; top: number; x1: number; bottom: number };
  lines: LineBox[];
  snippet: string;
  score: number;
}
export interface PdfChatResponse {
  answer: string;
  citations: PdfCitation[];
  found: boolean;
}
export interface PdfDocument {
  id: string;
  name: string;
  pages: number;
}

export async function fetchPdfDocuments(token: string): Promise<PdfDocument[]> {
  const res = await fetch(url("/pdfchat/documents"), {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Documents failed (${res.status})`);
  return (await res.json()).documents ?? [];
}

export async function sendPdfMessage(token: string, message: string): Promise<PdfChatResponse> {
  const res = await fetch(url("/pdfchat/message"), {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new Error(`Request failed (${res.status}): ${await res.text()}`);
  return res.json();
}

/** Fetch a PDF (auth-protected) as a blob object URL for the viewer. */
export async function fetchPdfBlobUrl(token: string, pdfId: string): Promise<string> {
  const res = await fetch(url(`/pdfchat/file/${pdfId}`), {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`PDF fetch failed (${res.status})`);
  return URL.createObjectURL(await res.blob());
}

/** Fetch a rendered page image (auth-protected) as a blob object URL. The image
 * is the same render the OCR boxes were measured against, so fractional highlight
 * coordinates align exactly. */
export async function fetchPdfPageUrl(token: string, pdfId: string, page: number): Promise<string> {
  const res = await fetch(url(`/pdfchat/page/${pdfId}/${page}`), {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Page render failed (${res.status})`);
  return URL.createObjectURL(await res.blob());
}

export async function sendMessage(
  token: string,
  message: string,
  sessionId: string | null
): Promise<ChatResponse> {
  const res = await fetch(url("/chat/message"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Request failed (${res.status}): ${detail}`);
  }
  return res.json();
}
