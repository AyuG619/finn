// Per-viewer preferences, kept in localStorage (wrapped: storage can be unavailable).
import { useEffect, useState } from "react";

export interface Prefs {
  windowKey: string; // "since" | "24" | "72" | "168" | "720"
  price_pct: number;
  vol_x: number;
  lookback: number;
  always: string[];
  symbols: string[] | null; // null = the demo portfolio
}

export const DEFAULT_PREFS: Prefs = {
  windowKey: "24",
  price_pct: 4,
  vol_x: 2,
  lookback: 20,
  always: ["results", "m_and_a", "management_change", "fund_raise", "litigation", "pledging"],
  symbols: null,
};

const KEY = "finn.prefs.v1";

export function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function write(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: preferences just won't persist */
  }
}

type Listener = (p: Prefs) => void;
let current: Prefs = { ...DEFAULT_PREFS, ...read<Partial<Prefs>>(KEY, {}) };
const listeners = new Set<Listener>();

export function getPrefs(): Prefs {
  return current;
}

export function setPrefs(patch: Partial<Prefs>): void {
  current = { ...current, ...patch };
  write(KEY, current);
  listeners.forEach((l) => l(current));
}

export function usePrefs(): [Prefs, (p: Partial<Prefs>) => void] {
  const [p, setP] = useState(current);
  useEffect(() => {
    const l = (n: Prefs) => setP(n);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return [p, setPrefs];
}

// "Since you last looked": remember when the brief was last opened.
const VISIT_KEY = "finn.lastVisit";
export function lastVisit(): number | null {
  return read<number | null>(VISIT_KEY, null);
}
export function markVisit(): void {
  write(VISIT_KEY, Date.now());
}

// Items the investor has cleared from the brief.
const SEEN_KEY = "finn.seen.v1";
export function seenSet(): Set<string> {
  return new Set(read<string[]>(SEEN_KEY, []));
}
export function markSeen(id: string, seen = true): void {
  const s = seenSet();
  if (seen) s.add(id);
  else s.delete(id);
  write(SEEN_KEY, Array.from(s).slice(-800));
}
