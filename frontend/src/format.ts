// Number and time formatting in the Indian investor's idiom (₹ crore, lakh crore, IST).

const MINUS = "−";

export function crore(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  const neg = v < 0;
  const a = Math.abs(v);
  let s: string;
  if (a >= 100000) s = `${(a / 100000).toLocaleString("en-IN", { maximumFractionDigits: 2 })} lakh cr`;
  else if (a >= 1000) s = `${Math.round(a).toLocaleString("en-IN")} cr`;
  else s = `${a.toLocaleString("en-IN", { maximumFractionDigits: digits ?? (a < 10 ? 2 : 1) })} cr`;
  return `${neg ? MINUS : ""}₹${s}`;
}

export function rupees(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: v < 100 ? 2 : v < 1000 ? 1 : 0 })}`;
}

export function pct(v: number | null | undefined, signed = true, digits = 1): string {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  const s = Math.abs(v).toFixed(digits);
  if (!signed) return `${v < 0 ? MINUS : ""}${s}%`;
  return `${v > 0 ? "+" : v < 0 ? MINUS : ""}${s}%`;
}

export function signed(v: number, digits = 0, unit = ""): string {
  const s = Math.abs(v).toFixed(digits);
  return `${v > 0 ? "+" : v < 0 ? MINUS : ""}${s}${unit}`;
}

export function num(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  return v.toLocaleString("en-IN", { maximumFractionDigits: digits });
}

export function tone(v: number | null | undefined): "up" | "down" | "" {
  if (v === null || v === undefined || !isFinite(v) || v === 0) return "";
  return v > 0 ? "up" : "down";
}

const IST = "Asia/Kolkata";

function istParts(d: Date) {
  const f = new Intl.DateTimeFormat("en-IN", { timeZone: IST, year: "numeric", month: "2-digit", day: "2-digit" });
  return f.format(d);
}

export function when(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const time = d.toLocaleTimeString("en-IN", { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: false });
  const today = istParts(now);
  const yest = istParts(new Date(now.getTime() - 86400000));
  const day = istParts(d);
  if (day === today) return time;
  if (day === yest) return `Yesterday ${time}`;
  const diff = (now.getTime() - d.getTime()) / 86400000;
  if (diff < 6 && diff > 0) return `${d.toLocaleDateString("en-IN", { timeZone: IST, weekday: "short" })} ${time}`;
  return d.toLocaleDateString("en-IN", { timeZone: IST, day: "numeric", month: "short" });
}

export function whenStacked(iso: string, now = new Date()): [string, string] {
  const d = new Date(iso);
  const time = d.toLocaleTimeString("en-IN", { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: false });
  const today = istParts(now);
  const day = istParts(d);
  if (day === today) return ["Today", time];
  if (day === istParts(new Date(now.getTime() - 86400000))) return ["Yday", time];
  return [d.toLocaleDateString("en-IN", { timeZone: IST, day: "numeric", month: "short" }), time];
}

export function dayLabel(isoDate: string): { date: string; dow: string } {
  const d = new Date(isoDate + "T12:00:00+05:30");
  return {
    date: d.toLocaleDateString("en-IN", { timeZone: IST, day: "numeric", month: "short" }),
    dow: d.toLocaleDateString("en-IN", { timeZone: IST, weekday: "long" }),
  };
}

export function ago(ms: number): string {
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export function windowLabel(hours: number): string {
  if (hours <= 24) return hours === 24 ? "last 24 hours" : `last ${Math.round(hours)} hours`;
  const d = Math.round(hours / 24);
  if (d === 7) return "last week";
  if (d === 30) return "last 30 days";
  return `last ${d} days`;
}

export function marketStatus(now = new Date()): { open: boolean; label: string } {
  const p = new Intl.DateTimeFormat("en-GB", { timeZone: IST, weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(now);
  const wd = p.find((x) => x.type === "weekday")?.value || "";
  const h = Number(p.find((x) => x.type === "hour")?.value || 0);
  const m = Number(p.find((x) => x.type === "minute")?.value || 0);
  const mins = h * 60 + m;
  const weekday = !["Sat", "Sun"].includes(wd);
  const open = weekday && mins >= 9 * 60 + 15 && mins < 15 * 60 + 30;
  const date = now.toLocaleDateString("en-IN", { timeZone: IST, weekday: "short", day: "numeric", month: "short" });
  return { open, label: open ? `${date} · market open` : `${date} · market closed` };
}
