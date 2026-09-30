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

/**
 * Every request to the backend goes through here.
 *
 * `credentials: "include"` sends the httpOnly auth cookie, which is the
 * preferred mechanism: a script injected into the page cannot read it. That
 * works when the frontend and backend share an origin â€” the recommended nginx
 * layout, where one host serves the app and proxies /auth, /chat, ... to the
 * backend.
 *
 * The Authorization header is still sent when we hold a token, for cross-origin
 * deployments (the GitHub Pages demo, or a separate API host) where the cookie
 * would be a third-party cookie and is blocked by default in several browsers.
 * The backend accepts either and prefers the cookie.
 */
async function authFetch(
  token: string | null,
  path: string,
  init: RequestInit = {}
): Promise<Response> {
  const headers = new Headers(init.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url(path), { ...init, headers, credentials: "include" });
}

/**
 * Ask the backend to renew the session using the cookie alone â€” no
 * Authorization header.
 *
 * Doubles as a capability probe. If this succeeds, cookie auth is working and
 * the app never needs to put a token in browser storage. If it fails, we are on
 * a cross-origin deployment and fall back to storing the token.
 */
export async function refreshFromCookie(): Promise<LoginResponse | null> {
  try {
    const res = await fetch(url("/auth/refresh"), {
      method: "POST",
      credentials: "include",
    });
    return res.ok ? await res.json() : null;
  } catch {
    return null; // offline or blocked â€” treat as "no cookie session"
  }
}

/** Renew a session we already hold a token for, to avoid an 8-hour cutoff. */
export async function refreshSession(token: string | null): Promise<LoginResponse | null> {
  try {
    const res = await authFetch(token, "/auth/refresh", { method: "POST" });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

/** Clear the auth cookie server-side. Safe to call with an expired token. */
export async function logout(): Promise<void> {
  try {
    await fetch(url("/auth/logout"), { method: "POST", credentials: "include" });
  } catch {
    // Signing out locally still proceeds.
  }
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
    credentials: "include", // let the browser store the httpOnly auth cookie
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
  const res = await authFetch(token, `/report/weekly?start=${start}&end=${end}`);
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
  const res = await authFetch(token, `/explorer?force=${force}`);
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
  const res = await authFetch(token, "/pdfchat/documents");
  if (!res.ok) throw new Error(`Documents failed (${res.status})`);
  return (await res.json()).documents ?? [];
}

export async function sendPdfMessage(token: string, message: string): Promise<PdfChatResponse> {
  const res = await authFetch(token, "/pdfchat/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new Error(`Request failed (${res.status}): ${await res.text()}`);
  return res.json();
}

/** Fetch a PDF (auth-protected) as a blob object URL for the viewer. */
export async function fetchPdfBlobUrl(token: string, pdfId: string): Promise<string> {
  const res = await authFetch(token, `/pdfchat/file/${pdfId}`);
  if (!res.ok) throw new Error(`PDF fetch failed (${res.status})`);
  return URL.createObjectURL(await res.blob());
}

/** Fetch a rendered page image (auth-protected) as a blob object URL. The image
 * is the same render the OCR boxes were measured against, so fractional highlight
 * coordinates align exactly. */
export async function fetchPdfPageUrl(token: string, pdfId: string, page: number): Promise<string> {
  const res = await authFetch(token, `/pdfchat/page/${pdfId}/${page}`);
  if (!res.ok) throw new Error(`Page render failed (${res.status})`);
  return URL.createObjectURL(await res.blob());
}

export async function sendMessage(
  token: string,
  message: string,
  sessionId: string | null
): Promise<ChatResponse> {
  const res = await authFetch(token, "/chat/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Request failed (${res.status}): ${detail}`);
  }
  return res.json();
}

