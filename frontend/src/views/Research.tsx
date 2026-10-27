import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Config, type ResearchState, type Signal } from "../api";
import { Columns, PairColumns } from "../charts";
import { crore, num, pct, rupees } from "../format";
import { IBack, ISend } from "../icons";
import { go } from "../router";
import { read, write } from "../store";
import { Skel } from "../ui";

const RECENT_KEY = "finn.recentResearch";

export default function Research({ symbol, config }: { symbol?: string; config: Config | null }) {
  if (!symbol) return <ResearchHome config={config} />;
  return <Dossier symbol={symbol.toUpperCase()} />;
}

// ------------------------------------------------------------------ search / home
function ResearchHome({ config }: { config: Config | null }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<{ symbol: string; name: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const recent = read<string[]>(RECENT_KEY, []);
  useEffect(() => {
    const s = q.trim();
    if (s.length < 2) {
      setResults([]);
      return;
    }
    const t = window.setTimeout(() => {
      api.search(s).then((r) => { setResults(r.results); setErr(null); }).catch((e) => setErr(e.message));
    }, 250);
    return () => window.clearTimeout(t);
  }, [q]);
  const submit = () => {
    const s = q.trim();
    if (!s) return;
    if (results[0] && (results[0].symbol.toLowerCase() === s.toLowerCase() || !/^[A-Z0-9&-]{2,15}$/i.test(s))) go("research", results[0].symbol);
    else go("research", s.toUpperCase());
  };
  return (
    <div className="view">
      <h1 className="h-page">Dig into a company</h1>
      <p className="lede">Any NSE-listed company. FINN reads its last four earnings calls and latest annual report, then holds what management promised against what the filed numbers show.</p>
      <form className="search" onSubmit={(e) => { e.preventDefault(); submit(); }} role="search">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ticker or company name, e.g. TRENT" aria-label="Company" autoCapitalize="characters" autoCorrect="off" spellCheck={false} />
        <button className="iconbtn primary" type="submit" aria-label="Research"><ISend /></button>
      </form>
      {results.length > 0 && (
        <div className="suggest">
          {results.slice(0, 6).map((r) => (
            <button key={r.symbol} onClick={() => go("research", r.symbol)}>
              <b className="cond">{r.symbol}</b>
              <span className="muted small">{r.name}</span>
            </button>
          ))}
        </div>
      )}
      {err && <p className="tiny muted">{err}</p>}
      <h2 className="h-sec">Worth a look</h2>
      <div className="picks">
        {(config?.research_set || []).map((r) => (
          <button className="pick" key={r.symbol} onClick={() => go("research", r.symbol)}>
            <span className="sym">{r.symbol}</span>
            <b>{r.name}</b>
            <span className="why">{r.why}</span>
          </button>
        ))}
      </div>
      {recent.length > 0 && (
        <>
          <h2 className="h-sec">You looked at</h2>
          <div className="chips">
            {recent.map((s) => <button key={s} className="chip" onClick={() => go("research", s)}>{s}</button>)}
          </div>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ dossier
const SECTIONS: [string, string][] = [
  ["numbers", "Numbers"],
  ["promises", "Promised vs delivered"],
  ["narrative", "Narrative vs numbers"],
  ["guidance", "Guidance"],
  ["bullbear", "Bull vs bear"],
  ["business", "Business"],
  ["trajectory", "Trajectory"],
  ["balance", "Balance sheet"],
  ["cash", "Cash quality"],
  ["owners", "Owners"],
  ["calls", "The calls"],
  ["sources", "Sources"],
];

function Dossier({ symbol }: { symbol: string }) {
  const [state, setState] = useState<ResearchState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let live = true;
    setState(null);
    setError(null);
    const tick = async (refresh = false) => {
      try {
        const s = await api.research(symbol, refresh);
        if (!live) return;
        setState(s);
        if (s.status === "running") timer.current = window.setTimeout(() => tick(), 2500);
        else if (s.status === "done") {
          const rec = read<string[]>(RECENT_KEY, []).filter((x) => x !== symbol);
          write(RECENT_KEY, [symbol, ...rec].slice(0, 8));
        }
      } catch (e: any) {
        if (!live) return;
        setError(e.message);
        timer.current = window.setTimeout(() => tick(), 6000);
      }
    };
    tick();
    return () => {
      live = false;
      window.clearTimeout(timer.current);
    };
  }, [symbol]);

  const r = state?.result || {};
  const co = r.company;
  const running = state?.status === "running";
  const notFound = state?.status === "error" && !co;

  return (
    <div className="view">
      <button className="btn quiet" style={{ paddingLeft: 0 }} onClick={() => go("research")}><IBack /> All companies</button>
      <div className="co-head">
        <div className="co-sym">{co?.symbol || symbol}</div>
        <div className="co-name">{co?.name || (state?.status === "error" ? "" : "Looking up the company…")}</div>
        {co && (
          <div className="co-meta">
            {co.sector?.Industry && <span>{co.sector.Industry}</span>}
            {co.price && <span>{rupees(co.price)}</span>}
            {co.market_cap_cr && <span>Market cap {crore(co.market_cap_cr)}</span>}
            <span>{co.basis === "consolidated" ? "Consolidated" : "Standalone"} figures</span>
          </div>
        )}
      </div>

      {error && !state && <div className="notice err">{error}</div>}
      {state?.status === "error" && <div className="notice err">{state.error}</div>}
      {notFound && <DidYouMean query={symbol} />}
      {notFound ? null : <>

      {state && (running || state.status === "error") && <Stages state={state} />}
      {state && state.status === "done" && <EngineLine state={state} />}

      {co && (
        <nav className="secnav" aria-label="Sections">
          <div className="chips">
            {SECTIONS.map(([id, label]) => (
              <button key={id} className="chip" onClick={() => document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}>{label}</button>
            ))}
          </div>
        </nav>
      )}

      {r.financials && <NumbersSec f={r.financials} />}
      <Section id="promises" title="Promised vs delivered" pending={running && !r.promises} empty={!running && !r.promises ? (r.analysis_note || "No earnings-call transcripts to analyse.") : null}>
        {r.promises && <Promises p={r.promises} />}
      </Section>
      <Section id="narrative" title="Narrative vs numbers" pending={running && !r.narrative} empty={!running && !r.narrative?.items?.length ? "Not enough material to compare." : null}>
        {r.narrative?.items?.length > 0 && <Narrative items={r.narrative.items} />}
      </Section>
      <Section id="guidance" title="What management is guiding to" pending={running && !r.guidance} empty={!running && !r.guidance?.items?.length ? "No explicit guidance in the recent calls." : null}>
        {r.guidance?.items?.length > 0 && <Guidance g={r.guidance} />}
      </Section>
      <Section id="bullbear" title="Bull vs bear" pending={running && !r.bull_bear} empty={!running && !r.bull_bear ? "Waiting on the analysis." : null}>
        {r.bull_bear && <BullBear b={r.bull_bear} />}
      </Section>
      <Section id="business" title="Business snapshot" pending={running && !r.business} empty={null}>
        {r.business && <Business b={r.business} />}
      </Section>
      {r.trajectory && <Trajectory t={r.trajectory} />}
      {r.balance_sheet && <Balance b={r.balance_sheet} />}
      {r.cash_quality && <Cash c={r.cash_quality} />}
      {r.shareholding && <Owners s={r.shareholding} />}
      {r.calls?.length > 0 && <Calls calls={r.calls} />}
      {r.sources && <Sources sources={r.sources} url={co?.screener_url} />}
      {co && <AskBox symbol={co.symbol} ready={state?.status === "done"} />}
      </>}
    </div>
  );
}

function DidYouMean({ query }: { query: string }) {
  const [hits, setHits] = useState<{ symbol: string; name: string }[] | null>(null);
  useEffect(() => {
    let live = true;
    api.search(query).then((r) => live && setHits(r.results.slice(0, 4))).catch(() => live && setHits([]));
    return () => {
      live = false;
    };
  }, [query]);
  if (!hits || !hits.length) return <p className="small muted">Try the company's NSE symbol (e.g. TRENT) or type its name in the bar below.</p>;
  return (
    <div className="card">
      <h3>Did you mean</h3>
      <div className="chips" style={{ flexWrap: "wrap" }}>
        {hits.map((h) => (
          <button key={h.symbol} className="chip" onClick={() => go("research", h.symbol)}>{h.symbol} · {h.name}</button>
        ))}
      </div>
    </div>
  );
}

function Section({ id, title, children, pending, empty }: { id: string; title: string; children?: ReactNode; pending?: boolean; empty?: string | null }) {
  return (
    <section className="sec" id={`sec-${id}`} aria-labelledby={`h-${id}`}>
      <div className="card">
        <h3 id={`h-${id}`}>{title}</h3>
        {pending ? <><p className="tiny muted" style={{ margin: 0 }}>FINN is reading the documents…</p><Skel /></> : empty ? <p className="small muted" style={{ margin: 0 }}>{empty}</p> : children}
      </div>
    </section>
  );
}

function Stages({ state }: { state: ResearchState }) {
  return (
    <div className="card" style={{ marginTop: 12 }} aria-live="polite">
      <h3>{state.status === "error" ? "Research stopped" : `Reading primary documents · ${Math.round(state.elapsed_s)}s`}</h3>
      <ul className="stages">
        {state.stages.map((s) => (
          <li key={s.id}>
            <span className={`stage-dot ${s.status}`} aria-hidden="true" />
            <span>
              {s.label}
              {s.detail && <span className="muted small"> · {s.detail}</span>}
            </span>
          </li>
        ))}
      </ul>
      <p className="tiny muted" style={{ marginBottom: 0 }}>Numbers appear first; the reading takes about a minute the first time, then it's cached.</p>
    </div>
  );
}

function EngineLine({ state }: { state: ResearchState }) {
  const e = state.result?.engine;
  if (!e) return null;
  const docs = (state.result?.sources || []).length;
  return (
    <p className="tiny muted" style={{ margin: "8px 0 0" }}>
      {state.cached ? "From cache. " : ""}Read {docs} primary document{docs === 1 ? "" : "s"} with {e.llm_calls} model call{e.llm_calls === 1 ? "" : "s"} ({num((e.input_tokens + e.output_tokens) / 1000, 0)}k tokens{e.cached_calls ? `, ${e.cached_calls} reused` : ""}) in {Math.round(e.seconds)}s. Models: {(e.models || []).map((m: string) => m.split(":")[1]).join(", ") || "none"}.
    </p>
  );
}

function Signals({ items }: { items: Signal[] }) {
  if (!items?.length) return null;
  return (
    <ul className="signals">
      {items.map((s, i) => (
        <li key={i}>
          <span className={`sigmark ${s.tone}`} aria-label={s.tone === "good" ? "Positive" : s.tone === "bad" ? "Concern" : "Note"}>{s.tone === "good" ? "+" : s.tone === "bad" ? "!" : "·"}</span>
          <span>{s.text}</span>
        </li>
      ))}
    </ul>
  );
}

function fmtMetric(v: number, unit: string) {
  if (unit === "cr") return Math.abs(v) >= 100000 ? `₹${(v / 100000).toLocaleString("en-IN", { maximumFractionDigits: 1 })}L cr` : crore(v);
  if (unit === "%") return `${num(v, 1)}%`;
  if (unit === "x") return `${num(v, 2)}×`;
  return num(v, 1);
}

function NumbersSec({ f }: { f: any }) {
  return (
    <section className="sec" id="sec-numbers">
      <div className="card">
        <h3>Financial snapshot</h3>
        <div className="tiles">
          {f.metrics.map((m: any) => (
            <div className="tile" key={m.label}>
              <div className="l">{m.label}</div>
              <div className="v">{fmtMetric(m.value, m.unit)}</div>
              {m.hint && <div className="h">{m.hint}</div>}
            </div>
          ))}
        </div>
        <Signals items={f.signals} />
        <div className="src">{f.source}</div>
      </div>
    </section>
  );
}

function Promises({ p }: { p: any }) {
  const items: any[] = p.items || [];
  const c = p.credibility;
  return (
    <>
      <div className="cred">
        {c ? (
          <>
            <div className="score">{num(c.score, 1)}<small>/10</small></div>
            <div className="tally">
              <b>{c.delivered}</b> delivered, <b>{c.partly}</b> partly, <b>{c.missed}</b> missed of {c.resolved} promises that can be scored{c.pending ? `; ${c.pending} still open` : ""}.
              {p.summary && <div style={{ marginTop: 4 }}>{p.summary}</div>}
            </div>
          </>
        ) : (
          <div className="tally">
            {p.model_score !== undefined && p.model_score !== null && <b>Analyst read: {p.model_score}/10. </b>}
            {p.summary || "Too few promises have come due to score management yet."}
            {items.length > 0 && <div className="muted tiny" style={{ marginTop: 4 }}>Not enough resolved, rupee-denominated promises for a computed score.</div>}
          </div>
        )}
      </div>
      {items.length === 0 ? (
        <p className="small muted">{p.note || "No explicit promises found in the last four calls."}</p>
      ) : (
        <ul className="ledger">
          {items.map((it, i) => (
            <li key={it.id || i}>
              <span className="made">{it.made_in}{it.due ? `, due ${it.due}` : ""}</span>
              <div>
                <div className="promise">{it.promise}</div>
                {it.quote && <div className="quote">“{it.quote}”</div>}
              </div>
              <span className={`stamp ${it.outcome}`} aria-label={`Outcome: ${it.outcome}`}>{it.outcome}</span>
              <div className="evidence">{it.evidence}</div>
              <div className="how">{it.computed ? "Scored by code against filed numbers" : "Judged by the model"}{it.source ? ` · ${it.source}` : ""}</div>
            </li>
          ))}
        </ul>
      )}
      <p className="tiny muted" style={{ marginBottom: 0 }}>{p.method}</p>
    </>
  );
}

function Narrative({ items }: { items: any[] }) {
  return (
    <ul className="nvn">
      {items.map((n, i) => (
        <li key={i}>
          <div className="claim">Management: “{n.claim}”</div>
          <div className="reality">
            {n.reality}
            <span className={`verdict ${n.verdict === "agrees" ? "up" : n.verdict === "diverges" ? "down" : "muted"}`}>
              {n.verdict === "agrees" ? "✓ agrees" : n.verdict === "diverges" ? "✗ diverges" : "~ mixed"}
            </span>
          </div>
          {n.source && <div className="tiny muted">{n.source}</div>}
        </li>
      ))}
    </ul>
  );
}

function Guidance({ g }: { g: any }) {
  return (
    <>
      <table className="tbl">
        <thead><tr><th>On</th><th style={{ textAlign: "left", paddingLeft: 10 }}>Guidance</th><th>For</th></tr></thead>
        <tbody>
          {g.items.map((x: any, i: number) => (
            <tr key={i}>
              <td style={{ verticalAlign: "top", fontWeight: 600 }}>{x.metric}</td>
              <td style={{ textAlign: "left", paddingLeft: 10 }}>{x.guidance}<div className="tiny muted">{x.source}</div></td>
              <td style={{ verticalAlign: "top" }}>{x.period}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {g.as_of && <div className="src">As of the {g.as_of}</div>}
    </>
  );
}

function BullBear({ b }: { b: any }) {
  return (
    <>
      <div className="cases">
        <div className="case">
          <h4 className="up">Bull case</h4>
          <ul>{b.bull.map((x: any, i: number) => <li key={i}>{x.point}<span className="ev">{x.evidence}</span></li>)}</ul>
        </div>
        <div className="case">
          <h4 className="down">Bear case</h4>
          <ul>{b.bear.map((x: any, i: number) => <li key={i}>{x.point}<span className="ev">{x.evidence}</span></li>)}</ul>
        </div>
      </div>
      {b.watch_next?.length > 0 && (
        <>
          <h4 style={{ margin: "14px 0 6px", fontSize: 14 }}>Check next quarter</h4>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>{b.watch_next.map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>
        </>
      )}
    </>
  );
}

function Business({ b }: { b: any }) {
  const segs: any[] = b.segments || [];
  return (
    <>
      {b.overview ? <p style={{ margin: 0 }}>{b.overview}</p> : <p className="small muted" style={{ margin: 0 }}>{b.note || "The annual report could not be read."}</p>}
      {segs.length > 0 && (
        <table className="tbl" style={{ marginTop: 10 }}>
          <thead><tr><th>Segment</th><th>Revenue</th></tr></thead>
          <tbody>
            {segs.map((s, i) => (
              <tr key={i}>
                <td>{s.name}<div className="tiny muted">{s.comment}</div></td>
                <td style={{ verticalAlign: "top" }}>{s.revenue_share_pct ? `${num(s.revenue_share_pct, 1)}%` : s.revenue_cr ? crore(s.revenue_cr) : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {b.revenue_mix && <p className="small ink2" style={{ marginBottom: 0 }}>{b.revenue_mix}</p>}
      {b.audit && (
        <p className="small ink2" style={{ marginBottom: 0 }}>
          Auditor's opinion: <b>{b.audit.opinion || "not stated"}</b>
          {b.audit.emphasis_of_matter ? `. Emphasis of matter: ${b.audit.emphasis_of_matter}` : ""}
          {b.audit.key_audit_matters?.length ? `. Key audit matters: ${b.audit.key_audit_matters.join("; ")}` : ""}
          {b.audit.contingent_liabilities ? `. Contingent liabilities: ${b.audit.contingent_liabilities}` : ""}
        </p>
      )}
      {b.risks?.length > 0 && (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 14 }}>Risks management itself flags</h4>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>{b.risks.map((r: any, i: number) => <li key={i}>{r.point}{r.page ? <span className="muted tiny"> p.{r.page}</span> : null}</li>)}</ul>
        </>
      )}
      {b.source && <div className="src">From the {b.source}{b.segments_source ? `; segments from the ${b.segments_source}` : ""}</div>}
    </>
  );
}

function Trajectory({ t }: { t: any }) {
  const g = t.growth || {};
  return (
    <section className="sec" id="sec-trajectory">
      <div className="card">
        <h3>Trajectory</h3>
        {Object.keys(g).length > 0 && (
          <table className="tbl">
            <thead><tr><th></th><th>3-yr CAGR</th><th>5-yr CAGR</th><th>Last year</th></tr></thead>
            <tbody>
              {Object.values(g).map((x: any) => (
                <tr key={x.name}><td>{x.name}</td><td>{pct(x.cagr_3y, false)}</td><td>{pct(x.cagr_5y, false)}</td><td>{pct(x.last_year)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        {t.quarterly?.length > 1 && (
          <Columns title="Revenue growth vs a year earlier, by quarter" polar data={t.quarterly.map((q: any) => ({ label: q.label, value: q.revenue_yoy }))} format={(v) => pct(v, true, 0)} />
        )}
        {t.annual?.length > 1 && (
          <Columns title="Operating margin by year" data={t.annual.map((a: any) => ({ label: a.label, value: a.opm }))} format={(v) => `${v.toFixed(1)}%`} />
        )}
        <Signals items={t.signals} />
        <div className="src">{t.source}</div>
      </div>
    </section>
  );
}

function Balance({ b }: { b: any }) {
  const years: any[] = b.years || [];
  const wc: any[] = (b.working_capital || []).filter((w: any) => w.ccc !== null);
  return (
    <section className="sec" id="sec-balance">
      <div className="card">
        <h3>Balance sheet</h3>
        {b.note && <p className="small muted" style={{ marginTop: 0 }}>{b.note}</p>}
        {years.some((y) => y.de !== null) && (
          <Columns title="Debt to equity" data={years.map((y) => ({ label: y.label, value: y.de }))} format={(v) => `${v.toFixed(2)}×`} />
        )}
        {wc.length > 1 && (
          <Columns title="Cash conversion cycle in days (shorter is better)" polar goodWhen="none" data={wc.map((w) => ({ label: w.label, value: w.ccc }))} format={(v) => `${Math.round(v)}d`} />
        )}
        <Signals items={b.signals} />
        <div className="src">{b.source}</div>
      </div>
    </section>
  );
}

function Cash({ c }: { c: any }) {
  const years: any[] = c.years || [];
  return (
    <section className="sec" id="sec-cash">
      <div className="card">
        <h3>Cash quality: do profits turn into cash?</h3>
        {c.note && <p className="small muted" style={{ marginTop: 0 }}>{c.note}</p>}
        {years.length > 1 && (
          <PairColumns title="Operating cash flow vs reported profit (₹ crore)" names={["Operating cash flow", "PAT"]} data={years.map((y) => ({ label: y.label, a: y.cfo, b: y.pat }))} format={(v) => crore(v)} />
        )}
        {years.some((y) => y.fcf !== null) && (
          <Columns title="Free cash flow (₹ crore)" polar data={years.map((y) => ({ label: y.label, value: y.fcf }))} format={(v) => crore(v)} />
        )}
        <Signals items={c.signals} />
        <div className="src">{c.source}</div>
      </div>
    </section>
  );
}

function Owners({ s }: { s: any }) {
  if (!s.rows?.length) return null;
  return (
    <section className="sec" id="sec-owners">
      <div className="card">
        <h3>Who owns it</h3>
        <table className="tbl">
          <thead><tr><th>Holder</th><th>Stake</th><th>Change, 1 yr</th></tr></thead>
          <tbody>
            {s.rows.map((r: any) => (
              <tr key={r.holder}><td>{r.holder}</td><td>{num(r.latest, 2)}%</td><td>{r.change_1y === null ? "–" : `${r.change_1y > 0 ? "+" : r.change_1y < 0 ? "−" : ""}${Math.abs(r.change_1y).toFixed(2)} pp`}</td></tr>
            ))}
          </tbody>
        </table>
        <Signals items={s.signals} />
        <div className="src">{s.source}{s.as_of ? `, as of ${s.as_of}` : ""}</div>
      </div>
    </section>
  );
}

function Calls({ calls }: { calls: any[] }) {
  return (
    <section className="sec" id="sec-calls">
      <div className="card">
        <h3>What analysts pressed on</h3>
        {calls.map((c) => (
          <div key={c.quarter} style={{ borderTop: "1px solid var(--rule-2)", padding: "10px 0" }}>
            <div className="small"><b>{c.quarter} call</b>{c.tone?.label ? <span className="muted"> · tone {c.tone.label}</span> : null}</div>
            {c.tone?.why && <div className="tiny muted">{c.tone.why}</div>}
            <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 14 }}>
              {(c.pushback || []).slice(0, 3).map((p: any, i: number) => (
                <li key={i}><b>{p.topic}.</b> {p.management_answer} <span className="tiny muted">p.{p.page}</span></li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function Sources({ sources, url }: { sources: any[]; url?: string }) {
  return (
    <section className="sec" id="sec-sources">
      <div className="card">
        <h3>Primary sources read</h3>
        {sources.length === 0 && <p className="small muted">screener.in lists no transcripts or annual reports for this company.</p>}
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>
          {sources.map((s, i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.label}</a></li>)}
        </ul>
        <p className="tiny muted" style={{ marginBottom: 0 }}>
          Analysis uses only the company's own filings, annual report and call transcripts. Standardised numbers from {url ? <a href={url} target="_blank" rel="noreferrer">screener.in</a> : "screener.in"}. No news, broker notes or model memory.
        </p>
      </div>
    </section>
  );
}

function AskBox({ symbol, ready }: { symbol: string; ready: boolean }) {
  const [q, setQ] = useState("");
  const [ans, setAns] = useState<{ q: string; a: string | null; note?: string; sources?: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (question: string) => {
    if (question.trim().length < 3) return;
    setBusy(true);
    try {
      const r = await api.ask(symbol, question.trim());
      setAns({ q: question, a: r.answer, note: r.note, sources: r.sources });
    } catch (e: any) {
      setAns({ q: question, a: null, note: e.message });
    } finally {
      setBusy(false);
    }
  };
  const examples = ["What did they guide on margins?", "Why did profit fall?", "What are the contingent liabilities?"];
  return (
    <section className="sec" id="sec-ask">
      <div className="card">
        <h3>Ask the documents</h3>
        <form className="search" style={{ marginTop: 0 }} onSubmit={(e) => { e.preventDefault(); submit(q); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Ask about ${symbol}`} aria-label={`Question about ${symbol}`} disabled={!ready} />
          <button className="iconbtn primary" type="submit" aria-label="Ask" disabled={busy || !ready}><ISend /></button>
        </form>
        <div className="chips" style={{ marginTop: 8 }}>
          {examples.map((e) => <button key={e} className="chip" disabled={!ready} onClick={() => { setQ(e); submit(e); }}>{e}</button>)}
        </div>
        {busy && <Skel />}
        {ans && !busy && (
          <div style={{ marginTop: 12 }}>
            <div className="tiny muted">{ans.q}</div>
            {ans.a ? <Answer text={ans.a} /> : <p className="small">{ans.note}</p>}
          </div>
        )}
        {!ready && <p className="tiny muted">Available once FINN has read the documents.</p>}
      </div>
    </section>
  );
}

export function Answer({ text }: { text: string }) {
  const paras = text.split(/\n{2,}/);
  return (
    <div className="answer">
      {paras.map((p, i) => (
        <p key={i}>
          {p.split(/(\[[^\]]{2,60}\]|\*\*[^*]+\*\*)/g).map((part, j) => {
            if (/^\[[^\]]+\]$/.test(part)) return <span key={j} className="cite">{part.slice(1, -1)}</span>;
            if (/^\*\*[^*]+\*\*$/.test(part)) return <b key={j}>{part.slice(2, -2)}</b>;
            return <span key={j}>{part}</span>;
          })}
        </p>
      ))}
    </div>
  );
}
