// Typed client for the FINN API (same origin).

export type Tier = "high" | "notable" | "routine";

export interface Metric {
  key: string;
  label: string;
  value: number | null;
  prev: number | null;
  year_ago: number | null;
  qoq: number | null;
  yoy: number | null;
}

export interface Person {
  name: string;
  role?: string;
  role_bucket?: string;
  change?: string;
  effective_date?: string | null;
  reason?: string | null;
  profile_from_filing?: string | null;
  linkedin_search: string;
  background?: {
    found: boolean;
    headline?: string;
    background?: string;
    previous_roles?: { org: string; title: string; years?: string | null }[];
    linkedin_url?: string | null;
    confidence?: string;
    sources?: { title: string; url: string }[];
  } | null;
}

export interface Enrichment {
  kind: "results" | "management" | "summary" | "error";
  filing_id: string;
  note?: string;
  // results
  quarter?: string;
  period_end?: string;
  metrics?: Metric[];
  margins?: { ebitda_margin?: number; ebitda_margin_qoq_bps?: number; ebitda_margin_yoy_bps?: number; pat_margin?: number };
  basis?: string;
  is_bank?: boolean;
  source?: string | null;
  verified?: { against: string; revenue_diff_pct: number; ok: boolean };
  filing_read_rejected?: string;
  pages?: number[];
  // management
  changes?: Person[];
  summary?: string | null;
  // summary
  one_liner?: string;
  key_facts?: { label: string; value: string }[];
  so_what?: string | null;
  llm?: { model: string; cached: boolean; input_tokens: number; output_tokens: number };
}

export interface FilingItem {
  type: "filing";
  id: string;
  symbol: string;
  name: string;
  weight: number;
  time: string;
  subject: string;
  detail: string | null;
  pdf_url: string | null;
  source: string;
  exchange_category: string;
  category: string;
  category_label: string;
  subtype: string | null;
  score: number;
  rank: number;
  tier: Tier;
  why: string[];
  watched?: boolean;
  amount_cr?: number | null;
  amount_pct_mcap?: number | null;
  enrichment?: Enrichment;
  related?: { id: string; subject: string; time: string; pdf_url: string | null; category_label: string }[];
}

export interface MoveItem {
  type: "move";
  id: string;
  symbol: string;
  name: string;
  weight: number;
  time: string;
  date: string;
  close: number;
  change_pct: number;
  volume: number;
  avg_volume: number | null;
  vol_ratio: number | null;
  provisional: boolean;
  hits: { kind: string; label: string; value: number }[];
  score: number;
  rank: number;
  tier: Tier;
  driver: { id: string; subject: string; category_label: string; time: string } | null;
  spark: number[];
  impact_bps: number;
}

export type DigestItem = FilingItem | MoveItem;

export interface Digest {
  generated_at: string;
  window: { hours: number; from: string; to: string };
  thresholds: { price_pct: number; vol_x: number; lookback: number };
  always: string[];
  headline: string;
  counts: { filings: number; high: number; notable: number; routine: number; moves: number; holdings: number };
  top: DigestItem[];
  notable: DigestItem[];
  routine: { label: string; count: number; items: FilingItem[] }[];
  upcoming: { symbol: string; name: string; date: string; filing_id: string; weight: number }[];
  alerts: string[];
  quiet: string[];
  source_errors: Record<string, string>;
}

export interface Holding {
  symbol: string;
  name: string;
  bse_code: string;
  sector: string;
  size: string;
  weight: number;
  price?: number;
  change_pct?: number | null;
  provisional?: boolean;
  as_of?: string;
  vol_ratio?: number | null;
  spark?: number[];
  high_52w?: number;
  low_52w?: number;
  range_pos?: number | null;
  ret_1m?: number | null;
  ret_1y?: number | null;
  error?: string;
}

export interface Config {
  portfolio: Holding[];
  research_set: { symbol: string; name: string; why: string }[];
  categories: Record<string, string>;
  default_always: string[];
  default_thresholds: { price_pct: number; vol_x: number; lookback: number };
  llm: { configured: { gemini: boolean; groq: boolean }; tiers: Record<string, { model: string; cooling_s: number }[]> };
  push_public_key: string;
}

export interface Signal { tone: "good" | "bad" | "neutral"; text: string }

export interface ResearchState {
  symbol: string;
  status: "running" | "done" | "error";
  stages: { id: string; label: string; status: string; detail: string | null }[];
  result: any;
  elapsed_s: number;
  error: string | null;
  cached?: boolean;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init?: RequestInit, timeoutMs = 60000): Promise<T> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(path, { ...init, signal: ctl.signal, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
    const text = await r.text();
    let body: any = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = null;
    }
    if (!r.ok) throw new ApiError(r.status, (body && (body.detail || body.note)) || `Request failed (${r.status})`);
    return body as T;
  } catch (e: any) {
    if (e instanceof ApiError) throw e;
    if (e?.name === "AbortError") throw new ApiError(0, "The server took too long to answer.");
    throw new ApiError(0, "Can't reach FINN's server. It may be waking up; free hosting sleeps when idle.");
  } finally {
    clearTimeout(t);
  }
}

export interface DigestParams {
  hours: number;
  price_pct: number;
  vol_x: number;
  lookback: number;
  always: string[];
  symbols?: string[] | null;
}

const qs = (o: Record<string, string | number | undefined | null>) =>
  Object.entries(o)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");

export const api = {
  config: () => req<Config>("/api/config"),
  digest: (p: DigestParams) =>
    req<Digest>(
      "/api/digest?" +
        qs({ hours: p.hours, price_pct: p.price_pct, vol_x: p.vol_x, lookback: p.lookback, always: p.always.join(","), symbols: p.symbols?.join(",") }),
      undefined,
      90000,
    ),
  enrich: (id: string) => req<Enrichment>("/api/enrich?" + qs({ id }), undefined, 120000),
  portfolio: (symbols?: string[] | null, lookback = 20) => req<{ holdings: Holding[]; day_change_pct: number }>("/api/portfolio?" + qs({ symbols: symbols?.join(","), lookback })),
  prices: (symbol: string) => req<{ bars: { date: string; close: number; volume: number }[]; source: string }>(`/api/prices/${encodeURIComponent(symbol)}`),
  search: (q: string) => req<{ results: { symbol: string; name: string }[] }>("/api/search?" + qs({ q })),
  research: (symbol: string, refresh = false) => req<ResearchState>(`/api/research/${encodeURIComponent(symbol)}` + (refresh ? "?refresh=true" : "")),
  ask: (symbol: string, question: string) =>
    req<{ answer: string | null; sources?: string[]; confidence?: string; note?: string; status?: string; retrieved?: string[] }>(
      "/api/ask",
      { method: "POST", body: JSON.stringify({ symbol, question }) },
      90000,
    ),
  usage: () => req<any>("/api/usage"),
  subscribe: (subscription: PushSubscriptionJSON, prefs: object) => req("/api/push/subscribe", { method: "POST", body: JSON.stringify({ subscription, prefs }) }),
  unsubscribe: (endpoint: string) => req("/api/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint }) }),
  pushTest: (endpoint: string) => req("/api/push/test", { method: "POST", body: JSON.stringify({ endpoint }) }),
};
