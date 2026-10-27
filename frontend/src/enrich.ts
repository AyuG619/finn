// Lazy enrichment of filings, with a small concurrency limit and a persistent cache
// (a filing never changes once published, so a good enrichment is kept).
import { useEffect, useState } from "react";
import { api, type Enrichment } from "./api";
import { read, write } from "./store";

const mem = new Map<string, Enrichment>();

// Drop cards cached by earlier versions (before results were cross-checked column by column).
try {
  for (let i = localStorage.length - 1; i >= 0; i--) {
    const k = localStorage.key(i);
    if (k && k.startsWith("finn.enr.")) localStorage.removeItem(k);
  }
} catch {
  /* storage unavailable */
}
const waiting: (() => void)[] = [];
let active = 0;
const MAX = 2;

function slot(): Promise<void> {
  if (active < MAX) {
    active++;
    return Promise.resolve();
  }
  return new Promise((res) => waiting.push(() => { active++; res(); }));
}
function release() {
  active--;
  const next = waiting.shift();
  if (next) next();
}

const inflight = new Map<string, Promise<Enrichment>>();

export function cachedEnrichment(id: string): Enrichment | undefined {
  if (mem.has(id)) return mem.get(id);
  const v = read<Enrichment | null>(`finn.enr3.${id}`, null);
  if (v) mem.set(id, v);
  return v || undefined;
}

export function loadEnrichment(id: string): Promise<Enrichment> {
  const hit = cachedEnrichment(id);
  if (hit) return Promise.resolve(hit);
  const existing = inflight.get(id);
  if (existing) return existing;
  const p = (async () => {
    await slot();
    try {
      const e = await api.enrich(id);
      const degraded = !!e.note || e.kind === "error" || (e.kind === "results" && !e.metrics);
      if (!degraded) {
        mem.set(id, e);
        // Provisional cards (e.g. results too fresh to cross-check) are re-checked by the server later.
        if (!e.provisional) write(`finn.enr3.${id}`, e);
      }
      return e;
    } finally {
      release();
      inflight.delete(id);
    }
  })();
  inflight.set(id, p);
  return p;
}

export function useEnrichment(id: string, initial: Enrichment | undefined, enabled: boolean) {
  const [state, setState] = useState<{ data?: Enrichment; loading: boolean; error?: string }>(() => {
    const c = initial && !initial.note ? initial : cachedEnrichment(id);
    return { data: c, loading: false };
  });
  useEffect(() => {
    if (!enabled || (state.data && !state.data.note)) return;
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    loadEnrichment(id)
      .then((d) => live && setState({ data: d, loading: false }))
      .catch((e) => live && setState((s) => ({ ...s, loading: false, error: e?.message || "Couldn't read this filing." })));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, enabled]);
  return state;
}
