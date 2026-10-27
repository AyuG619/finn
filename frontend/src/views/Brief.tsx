import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Digest, type DigestItem, type FilingItem } from "../api";
import { ago, dayLabel, pct, tone, when, windowLabel } from "../format";
import { FilingSlip, MoveSlip, RegisterRow } from "../slips";
import { lastVisit, markSeen, markVisit, read, seenSet, usePrefs, write } from "../store";

const WINDOWS: { key: string; label: string; hours: number }[] = [
  { key: "24", label: "24 hours", hours: 24 },
  { key: "72", label: "3 days", hours: 72 },
  { key: "168", label: "Week", hours: 168 },
  { key: "720", label: "Month", hours: 720 },
];

// The visit before this one: "since you last looked" compares against it.
const PREV_VISIT = lastVisit();

function sinceHours(): number | null {
  if (!PREV_VISIT) return null;
  const h = (Date.now() - PREV_VISIT) / 3600000;
  if (h < 1 || h > 24 * 30) return null;
  return Math.max(1, Math.ceil(h));
}

export default function Brief({ focusId }: { focusId?: string }) {
  const [prefs, setPrefs] = usePrefs();
  const since = sinceHours();
  const hours = prefs.windowKey === "since" ? since ?? 24 : Number(prefs.windowKey) || 24;
  const params = useMemo(
    () => ({ hours, price_pct: prefs.price_pct, vol_x: prefs.vol_x, lookback: prefs.lookback, always: prefs.always, symbols: prefs.symbols }),
    [hours, prefs.price_pct, prefs.vol_x, prefs.lookback, prefs.always, prefs.symbols],
  );
  const cacheKey = "finn.digest." + JSON.stringify(params);
  const [digest, setDigest] = useState<Digest | null>(() => read<Digest | null>(cacheKey, null));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [book, setBook] = useState<{ day_change_pct: number; provisional: boolean; as_of?: string } | null>(null);
  const [seen, setSeen] = useState<Set<string>>(() => seenSet());
  const [openRow, setOpenRow] = useState<string | null>(focusId || null);
  const [foldOpen, setFoldOpen] = useState<string | null>(null);
  const retries = useRef(0);

  useEffect(() => {
    const t = window.setTimeout(markVisit, 4000);
    return () => window.clearTimeout(t);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api.digest(params);
      setDigest(d);
      write(cacheKey, d);
      setError(null);
      retries.current = 0;
    } catch (e: any) {
      setError(e?.message || "Couldn't load the brief.");
      if (e?.status === 0 && retries.current < 6) {
        retries.current += 1;
        window.setTimeout(load, 8000);
      }
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey]);

  useEffect(() => {
    setDigest(read<Digest | null>(cacheKey, null));
    load();
  }, [cacheKey, load]);

  useEffect(() => {
    api
      .portfolio(prefs.symbols, prefs.lookback)
      .then((p) => setBook({ day_change_pct: p.day_change_pct, provisional: p.holdings.some((h) => h.provisional), as_of: p.holdings[0]?.as_of }))
      .catch(() => setBook(null));
  }, [prefs.symbols, prefs.lookback]);

  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "visible" && digest && Date.now() - new Date(digest.generated_at).getTime() > 5 * 60000) load();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [digest, load]);

  useEffect(() => {
    if (!focusId || !digest) return;
    const el = document.getElementById(`item-${focusId}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusId, digest]);

  const toggleSeen = (id: string, v: boolean) => {
    markSeen(id, v);
    setSeen(seenSet());
  };
  const openDriver = (id: string) => {
    setOpenRow(id);
    window.setTimeout(() => document.getElementById(`item-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
  };

  const top = digest?.top || [];
  const unseenTop = top.filter((x) => !seen.has(x.id));
  const seenTop = top.filter((x) => seen.has(x.id));
  const notable = digest?.notable || [];

  return (
    <div className="view">
      <div className="chips" role="group" aria-label="Time window">
        {since && (
          <button className="chip" aria-pressed={prefs.windowKey === "since"} onClick={() => setPrefs({ windowKey: "since" })}>
            Since you last looked
          </button>
        )}
        {WINDOWS.map((w) => (
          <button key={w.key} className="chip" aria-pressed={prefs.windowKey === w.key} onClick={() => setPrefs({ windowKey: w.key })}>
            {w.label}
          </button>
        ))}
      </div>

      <Headline digest={digest} unseen={unseenTop.length} />

      <p className="small ink2" style={{ margin: "0 0 4px" }}>
        {book && (
          <>
            Your book {book.provisional ? "today, live:" : "at the last close:"}{" "}
            <b className={tone(book.day_change_pct)}>{pct(book.day_change_pct, true, 2)}</b> ·{" "}
          </>
        )}
        {digest ? (
          <>
            {digest.counts.filings} filings and {digest.counts.moves} unusual move{digest.counts.moves === 1 ? "" : "s"} across {digest.counts.holdings} holdings, {windowLabel(digest.window.hours)}
          </>
        ) : (
          "Reading the exchange feeds…"
        )}
      </p>
      <p className="tiny muted" style={{ margin: 0 }}>
        {loading ? "Updating…" : digest ? `Updated ${ago(Date.now() - new Date(digest.generated_at).getTime())}` : ""}
        {digest && Object.keys(digest.source_errors).length > 0 && ` · feed unavailable for ${Object.keys(digest.source_errors).join(", ")}`}
      </p>

      {error && !digest && <div className="notice err">{error} {retries.current > 0 && "Retrying…"}</div>}
      {error && digest && <div className="notice">Showing the last brief. {error}</div>}

      <div className={loading && digest ? "stale" : ""}>
        {digest && unseenTop.length === 0 && (
          <div className="empty" style={{ marginTop: 18 }}>
            <h3>{top.length ? "You're through what matters." : "Nothing needs you."}</h3>
            <p>
              {digest.counts.routine} routine filing{digest.counts.routine === 1 ? "" : "s"} folded below.
              {digest.counts.filings === 0 && hours <= 24 && " Try the 3-day window for more context."}
            </p>
          </div>
        )}

        {unseenTop.length > 0 && (
          <>
            <h2 className="h-sec">Needs you <span className="count">{unseenTop.length}</span></h2>
            <div className="slips deal">
              {unseenTop.map((x) => <Slip key={x.id} x={x} seen={false} onSeen={toggleSeen} onDriver={openDriver} focusId={focusId} />)}
            </div>
          </>
        )}

        {notable.length > 0 && (
          <>
            <h2 className="h-sec">Worth a glance <span className="count">{notable.length}</span></h2>
            <div className="register">
              {notable.map((x) => (
                <RegisterRow key={x.id} item={x} open={openRow === x.id} onToggle={() => setOpenRow(openRow === x.id ? null : x.id)} highlight={focusId === x.id} />
              ))}
            </div>
          </>
        )}

        {digest && digest.routine.length > 0 && (
          <>
            <h2 className="h-sec">Folded away <span className="count">{digest.counts.routine}</span></h2>
            <div className="fold">
              <div className="fold-line">Routine paperwork FINN decided you don't need to read.</div>
              <div className="fold-groups">
                {digest.routine.map((g) => (
                  <button key={g.label} className="chip soft" aria-pressed={foldOpen === g.label} onClick={() => setFoldOpen(foldOpen === g.label ? null : g.label)}>
                    {g.label} · {g.count}
                  </button>
                ))}
              </div>
              {foldOpen && (
                <ul className="fold-list">
                  {digest.routine.find((g) => g.label === foldOpen)?.items.map((f: FilingItem) => (
                    <li key={f.id}>
                      <span className="tiny muted">{when(f.time)}</span>
                      <span>
                        <b className="cond">{f.symbol}</b>{" "}
                        {f.pdf_url ? <a href={f.pdf_url} target="_blank" rel="noreferrer">{f.detail || f.subject}</a> : f.detail || f.subject}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}

        {seenTop.length > 0 && (
          <>
            <h2 className="h-sec">Seen <span className="count">{seenTop.length}</span></h2>
            <div className="slips">
              {seenTop.map((x) => <Slip key={x.id} x={x} seen onSeen={toggleSeen} onDriver={openDriver} />)}
            </div>
          </>
        )}

        {digest && digest.upcoming.length > 0 && (
          <>
            <h2 className="h-sec">Results coming up</h2>
            <div className="cal">
              {groupByDate(digest.upcoming).map(([date, names]) => {
                const l = dayLabel(date);
                return (
                  <div className="cal-day" key={date}>
                    <div className="cal-date">{l.date}</div>
                    <div className="cal-dow">{l.dow}</div>
                    <div className="cal-names">{names.join(", ")}</div>
                  </div>
                );
              })}
            </div>
          </>
        )}

        {digest && digest.quiet.length > 0 && (
          <p className="small muted" style={{ marginTop: 22 }}>
            Nothing from {digest.quiet.join(", ")} in this window.
          </p>
        )}
      </div>
    </div>
  );
}

function shortCat(label: string): string {
  if (label === label.toUpperCase()) return label; // M&A, AGM / EGM
  const l = label.toLowerCase();
  return l === "litigation & regulatory" ? "regulatory matter" : l === "routine paperwork" ? "filing" : l;
}

/** "Maruti Suzuki results and M&A", not "Maruti Suzuki results and Maruti Suzuki M&A". */
function headlineLabels(items: DigestItem[]): string[] {
  const by = new Map<string, string[]>();
  for (const x of items) {
    const part = x.type === "move" ? pct(x.change_pct) : shortCat(x.category_label);
    by.set(x.name, [...(by.get(x.name) || []), part]);
  }
  return Array.from(by.entries()).map(([name, parts]) => `${name} ${listJoin(parts)}`);
}

export function listJoin(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

function groupByDate(up: Digest["upcoming"]): [string, string[]][] {
  const m = new Map<string, string[]>();
  for (const u of up) m.set(u.date, [...(m.get(u.date) || []), u.symbol]);
  return Array.from(m.entries());
}

function Slip({ x, seen, onSeen, onDriver, focusId }: { x: DigestItem; seen: boolean; onSeen: (id: string, v: boolean) => void; onDriver: (id: string) => void; focusId?: string }) {
  if (x.type === "move") return <MoveSlip item={x} seen={seen} onSeen={(v) => onSeen(x.id, v)} onDriver={onDriver} highlight={focusId === x.id} />;
  return <FilingSlip item={x} seen={seen} onSeen={(v) => onSeen(x.id, v)} highlight={focusId === x.id} />;
}

function Headline({ digest, unseen }: { digest: Digest | null; unseen: number }) {
  if (!digest) return <h1 className="h-brief">Reading today's filings…</h1>;
  const top = digest.top;
  if (!top.length) return <h1 className="h-brief"><span className="lead">Nothing needs you.</span></h1>;
  const labels = headlineLabels(top.slice(0, 3));
  if (top.length > 3) labels.push(`${top.length - 3} more`);
  return (
    <h1 className="h-brief">
      <span className="lead">
        {unseen === 0 ? "All clear." : `${unseen} thing${unseen === 1 ? "" : "s"} need${unseen === 1 ? "s" : ""} you.`}
      </span>
      <span className="subjects">{listJoin(labels)}</span>
    </h1>
  );
}
