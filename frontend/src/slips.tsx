// The anatomy of each kind of pushed item. Results get figures, management changes get people,
// moves get a sparkline and a likely driver, everything else gets one specific line.
import { useRef, useState, type ReactNode } from "react";
import type { Enrichment, FilingItem, MoveItem, Person } from "./api";
import { Spark } from "./charts";
import { useEnrichment } from "./enrich";
import { pct, rupees, signed, tone, when } from "./format";
import { IDoc } from "./icons";
import { go } from "./router";
import { Skel } from "./ui";

export function Delta({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="delta muted">n.m.</span>;
  return <span className={`delta ${tone(v)}`}>{pct(v)}</span>;
}

export function ResultsFigures({ e }: { e: Enrichment }) {
  if (!e.metrics || !e.metrics.length) return <p className="slip-line ink2">{e.note || "Numbers not available yet."}</p>;
  const m = e.margins || {};
  return (
    <>
      <table className="figs">
        <thead>
          <tr>
            <th>{e.quarter}</th>
            <th>₹ crore</th>
            <th>QoQ</th>
            <th>YoY</th>
          </tr>
        </thead>
        <tbody>
          {e.metrics.map((r) => (
            <tr key={r.key}>
              <td>{r.label}</td>
              <td className="v">{r.value === null ? "–" : Math.round(r.value).toLocaleString("en-IN")}</td>
              <td><Delta v={r.qoq} /></td>
              <td><Delta v={r.yoy} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {m.ebitda_margin !== undefined && (
        <p className="slip-why" style={{ color: "var(--ink-2)" }}>
          {e.is_bank ? "Pre-provision margin" : e.source?.startsWith("screener") ? "Operating margin" : "EBITDA margin"} {m.ebitda_margin.toFixed(1)}%
          {m.ebitda_margin_yoy_bps !== undefined && <> · {signed(m.ebitda_margin_yoy_bps, 0, " bps")} YoY</>}
          {m.ebitda_margin_qoq_bps !== undefined && <> · {signed(m.ebitda_margin_qoq_bps, 0, " bps")} QoQ</>}
        </p>
      )}
      <p className="verify">
        {e.verified ? (
          e.verified.ok ? (
            <><span className="ok">✓ Cross-checked.</span> Revenue matches screener.in within {e.verified.revenue_diff_pct.toFixed(1)}%. </>
          ) : (
            <><span className="warn">Check this.</span> Revenue differs from screener.in by {e.verified.revenue_diff_pct.toFixed(1)}%. </>
          )
        ) : (
          <>Source: {e.source || "filing"}. </>
        )}
        {e.basis && <>{e.basis[0].toUpperCase() + e.basis.slice(1)} figures{e.pages && e.pages.length > 0 && e.source === "results filing" ? `, read from p.${e.pages.join(", ")} of the filing` : ""}.</>}
      </p>
    </>
  );
}

function PersonCard({ p }: { p: Person }) {
  const incoming = ["appointed", "re-designated", "re-appointed"].includes(p.change || "");
  const bg = p.background;
  return (
    <div className="person">
      <div className="change-line" style={{ marginTop: 0 }}>
        <span className={`change-kind ${incoming ? "in" : "out"}`}>{incoming ? "Incoming" : p.change === "demise" ? "Demise" : "Outgoing"}</span>
        <span>
          <span className="person-name">{p.name}</span>
          <span className="person-role"> · {p.role}</span>
        </span>
      </div>
      {p.effective_date && <div className="person-role">From {p.effective_date}</div>}
      {p.reason && <p>Reason given: {p.reason}</p>}
      {bg && bg.found ? (
        <>
          {bg.headline && <div className="person-head">{bg.headline}</div>}
          {bg.background && <p>{bg.background}</p>}
          {bg.previous_roles && bg.previous_roles.length > 0 && (
            <ul className="roles">
              {bg.previous_roles.map((r, i) => (
                <li key={i}>
                  {r.title}, {r.org}
                  {r.years ? <span className="muted"> · {r.years}</span> : null}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : p.profile_from_filing ? (
        <p>{p.profile_from_filing} <span className="muted">(from the company's disclosure)</span></p>
      ) : null}
      <div className="btnrow" style={{ marginTop: 8 }}>
        {bg?.linkedin_url ? (
          <a className="btn quiet" href={bg.linkedin_url} target="_blank" rel="noreferrer">LinkedIn profile</a>
        ) : (
          <a className="btn quiet" href={p.linkedin_search} target="_blank" rel="noreferrer">Find on LinkedIn</a>
        )}
        {bg?.sources?.slice(0, 2).map((s, i) => (
          <a key={i} className="btn quiet" href={s.url} target="_blank" rel="noreferrer">{s.title}</a>
        ))}
      </div>
    </div>
  );
}

export function EnrichmentBody({ item, e, loading, error }: { item: FilingItem; e?: Enrichment; loading: boolean; error?: string }) {
  if (!e) {
    return (
      <>
        <p className="slip-line">{item.detail || item.subject}</p>
        {loading && (<><p className="tiny muted" style={{ margin: "8px 0 0" }}>Reading the filing…</p><Skel /></>)}
        {error && <p className="tiny muted">{error}</p>}
      </>
    );
  }
  if (e.kind === "results") return <ResultsFigures e={e} />;
  if (e.kind === "management") {
    const people = (e.changes || []).slice().sort((a, b) => Number(["appointed", "re-designated"].includes(b.change || "")) - Number(["appointed", "re-designated"].includes(a.change || "")));
    return (
      <>
        <p className="slip-line">{e.summary || item.detail || item.subject}</p>
        {people.slice(0, 3).map((p, i) => <PersonCard key={i} p={p} />)}
        {e.note && <p className="tiny muted">{e.note}</p>}
      </>
    );
  }
  return (
    <>
      <p className="slip-line">{e.one_liner || item.detail || item.subject}</p>
      {e.key_facts && e.key_facts.length > 0 && (
        <dl className="facts">
          {e.key_facts.map((k, i) => (
            <FactRow key={i} label={k.label} value={k.value} />
          ))}
        </dl>
      )}
      {e.so_what && <p className="slip-why" style={{ color: "var(--ink-2)" }}>{e.so_what}</p>}
      {e.note && <p className="tiny muted">{e.note}</p>}
    </>
  );
}

function FactRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function useSwipe(onDone: () => void) {
  const start = useRef<{ x: number; y: number; id: number } | null>(null);
  const [dx, setDx] = useState(0);
  return {
    dx,
    handlers: {
      onPointerDown: (ev: React.PointerEvent) => {
        if (ev.pointerType === "mouse") return;
        start.current = { x: ev.clientX, y: ev.clientY, id: ev.pointerId };
      },
      onPointerMove: (ev: React.PointerEvent) => {
        const s = start.current;
        if (!s || s.id !== ev.pointerId) return;
        const x = ev.clientX - s.x;
        const y = ev.clientY - s.y;
        if (Math.abs(y) > Math.abs(x) && Math.abs(dx) < 8) {
          start.current = null;
          setDx(0);
          return;
        }
        if (x > 0) setDx(x);
      },
      onPointerUp: () => {
        if (dx > 96) onDone();
        start.current = null;
        setDx(0);
      },
      onPointerCancel: () => {
        start.current = null;
        setDx(0);
      },
    },
  };
}

function SlipShell({ children, onSeen, seen, id, highlight }: { children: ReactNode; onSeen: () => void; seen: boolean; id: string; highlight?: boolean }) {
  const { dx, handlers } = useSwipe(onSeen);
  return (
    <article
      className={`slip${seen ? " seen" : ""}`}
      id={`item-${id}`}
      {...handlers}
      style={{ transform: dx ? `translateX(${Math.min(dx, 160)}px)` : undefined, outline: highlight ? "2px solid var(--blue)" : undefined }}
    >
      {dx > 20 && <div className="swipe-hint" style={{ transform: `translateX(-${Math.min(dx, 160)}px)` }}>Seen</div>}
      {children}
    </article>
  );
}

export function FilingSlip({ item, seen, onSeen, highlight }: { item: FilingItem; seen: boolean; onSeen: (v: boolean) => void; highlight?: boolean }) {
  const { data, loading, error } = useEnrichment(item.id, item.enrichment, !seen);
  const label = item.category === "results" && data?.quarter ? `Results · ${data.quarter}` : item.category_label;
  return (
    <SlipShell id={item.id} seen={seen} onSeen={() => onSeen(true)} highlight={highlight}>
      <div className="slip-head">
        <span className="tag">{label}</span>
        {item.watched && <span className="watched-badge">Watched</span>}
        <span className="time">{when(item.time)}</span>
      </div>
      <div className="slip-title">
        <span className="sym">{item.symbol}</span>
        <span className="name">{item.name}</span>
      </div>
      <EnrichmentBody item={item} e={data} loading={loading} error={error} />
      {item.related && item.related.length > 0 && (
        <p className="slip-why">Also filed: {item.related.map((r) => r.category_label === "Results" ? r.subject : r.category_label).slice(0, 3).join(", ")}</p>
      )}
      <p className="slip-why">Why it's here: {item.why.filter(Boolean).slice(0, 3).join("; ")}{item.weight ? `; ${item.weight}% of your book` : ""}</p>
      <div className="slip-foot">
        {item.pdf_url && (
          <a className="btn" href={item.pdf_url} target="_blank" rel="noreferrer"><IDoc /> Filing</a>
        )}
        <button className="btn" onClick={() => go("research", item.symbol)}>Research {item.symbol}</button>
        <span className="spacer" />
        <button className="btn quiet" onClick={() => onSeen(!seen)}>{seen ? "Undo" : "Seen"}</button>
      </div>
    </SlipShell>
  );
}

export function MoveSlip({ item, seen, onSeen, onDriver, highlight }: { item: MoveItem; seen: boolean; onSeen: (v: boolean) => void; onDriver: (id: string) => void; highlight?: boolean }) {
  const up = item.change_pct >= 0;
  return (
    <SlipShell id={item.id} seen={seen} onSeen={() => onSeen(true)} highlight={highlight}>
      <div className="slip-head">
        <span className="tag">Unusual move</span>
        <span>{item.provisional ? "· intraday, provisional until the 15:30 close" : `· close of ${new Date(item.date + "T12:00:00+05:30").toLocaleDateString("en-IN", { day: "numeric", month: "short" })}`}</span>
      </div>
      <div className="move-top">
        <div>
          <div className="slip-title">
            <span className="sym">{item.symbol}</span>
            <span className="name">{item.name}</span>
          </div>
          <div className={`move-chg ${up ? "up" : "down"}`}>{pct(item.change_pct)}</div>
          <div className="small ink2">{rupees(item.close)}</div>
        </div>
        <Spark values={item.spark} width={120} height={44} />
      </div>
      <div className="pills">
        {item.hits.map((h) => (
          <span key={h.kind} className={`pill ${h.kind === "price" ? (up ? "green" : "red") : h.kind === "volume" ? "blue" : h.kind === "low_52w" ? "red" : "green"}`}>{h.label}</span>
        ))}
        {item.vol_ratio !== null && !item.hits.some((h) => h.kind === "volume") && <span className="pill">{item.vol_ratio.toFixed(1)}× usual volume{item.provisional ? " so far" : ""}</span>}
      </div>
      {item.driver ? (
        <button className="driver" onClick={() => onDriver(item.driver!.id)}>
          Likely driver: <b>{item.driver.category_label}</b> · {item.driver.subject} <span className="muted">({when(item.driver.time)})</span>
        </button>
      ) : (
        <p className="slip-why">No filing from the company explains this move.</p>
      )}
      <p className="slip-why">
        Effect on your book ≈ {signed(item.impact_bps, 0, " bps")} ({item.weight}% weight)
      </p>
      <div className="slip-foot">
        <button className="btn" onClick={() => go("research", item.symbol)}>Research {item.symbol}</button>
        <span className="spacer" />
        <button className="btn quiet" onClick={() => onSeen(!seen)}>{seen ? "Undo" : "Seen"}</button>
      </div>
    </SlipShell>
  );
}

export function RegisterRow({ item, open, onToggle, highlight }: { item: FilingItem | MoveItem; open: boolean; onToggle: () => void; highlight?: boolean }) {
  if (item.type === "move") {
    return (
      <div className="reg" id={`item-${item.id}`} onClick={onToggle} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onToggle()} style={{ outline: highlight ? "2px solid var(--blue)" : undefined }}>
        <div className="reg-time">{item.provisional ? "Live" : new Date(item.date + "T12:00:00+05:30").toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</div>
        <div className="reg-main">
          <div className="reg-top">
            <span className="reg-sym">{item.symbol}</span>
            <span className={`small ${tone(item.change_pct)}`} style={{ fontWeight: 700 }}>{pct(item.change_pct)}</span>
            <span className="reg-cat">{item.hits.map((h) => h.label).join(" · ")}</span>
          </div>
          {open && (
            <div className="reg-open">
              <Spark values={item.spark} width={180} height={40} />
              <p className="slip-why">{item.driver ? `Likely driver: ${item.driver.subject}` : "No filing explains this move."}</p>
              <button className="btn" onClick={(e) => { e.stopPropagation(); go("research", item.symbol); }}>Research {item.symbol}</button>
            </div>
          )}
        </div>
      </div>
    );
  }
  return <FilingRow item={item} open={open} onToggle={onToggle} highlight={highlight} />;
}

function FilingRow({ item, open, onToggle, highlight }: { item: FilingItem; open: boolean; onToggle: () => void; highlight?: boolean }) {
  const { data, loading, error } = useEnrichment(item.id, item.enrichment, open);
  const raw = (data && (data.one_liner || data.summary)) || item.detail || item.subject;
  const line = raw && raw.trim().toLowerCase() !== item.category_label.toLowerCase() ? raw : "Tap for FINN's read of the filing.";
  return (
    <div className={`reg${item.watched ? " watched" : ""}`} id={`item-${item.id}`} onClick={onToggle} role="button" tabIndex={0} aria-expanded={open}
      onKeyDown={(e) => e.key === "Enter" && onToggle()} style={{ outline: highlight ? "2px solid var(--blue)" : undefined }}>
      <div className="reg-time">{when(item.time)}</div>
      <div className="reg-main">
        <div className="reg-top">
          <span className="reg-sym">{item.symbol}</span>
          <span className="reg-cat">{item.category_label}</span>
        </div>
        {!open && <div className="reg-text">{line}</div>}
        {open && (
          <div onClick={(e) => e.stopPropagation()}>
            <EnrichmentBody item={item} e={data} loading={loading} error={error} />
            <p className="slip-why">Why it's here: {item.why.slice(0, 2).join("; ")}</p>
            <div className="btnrow">
              {item.pdf_url && <a className="btn" href={item.pdf_url} target="_blank" rel="noreferrer"><IDoc /> Filing</a>}
              <button className="btn" onClick={() => go("research", item.symbol)}>Research {item.symbol}</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
