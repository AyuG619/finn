import { useEffect, useMemo, useState } from "react";
import { api, type Digest, type Holding } from "../api";
import { Line, Spark } from "../charts";
import { pct, rupees, tone, when } from "../format";
import { go } from "../router";
import { read, usePrefs, write } from "../store";
import { Skel, useUI } from "../ui";

type Sort = "weight" | "move" | "volume";

export default function Book() {
  const [prefs] = usePrefs();
  const key = "finn.book." + (prefs.symbols || []).join(",");
  const [data, setData] = useState<{ holdings: Holding[]; day_change_pct: number } | null>(() => read(key, null));
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("weight");
  const ui = useUI();

  useEffect(() => {
    let live = true;
    api
      .portfolio(prefs.symbols, prefs.lookback)
      .then((d) => {
        if (!live) return;
        setData(d);
        write(key, d);
        setError(null);
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [key, prefs.symbols, prefs.lookback]);

  const rows = useMemo(() => {
    const h = [...(data?.holdings || [])];
    if (sort === "move") h.sort((a, b) => Math.abs(b.change_pct || 0) - Math.abs(a.change_pct || 0));
    else if (sort === "volume") h.sort((a, b) => (b.vol_ratio || 0) - (a.vol_ratio || 0));
    else h.sort((a, b) => b.weight - a.weight);
    return h;
  }, [data, sort]);

  const live = data?.holdings.some((h) => h.provisional);
  const contributors = useMemo(() => {
    const hs = (data?.holdings || []).filter((h) => h.change_pct !== null && h.change_pct !== undefined);
    return hs
      .map((h) => ({ sym: h.symbol, bps: (h.change_pct || 0) * h.weight }))
      .sort((a, b) => Math.abs(b.bps) - Math.abs(a.bps))
      .slice(0, 3);
  }, [data]);

  return (
    <div className="view">
      <h1 className="h-page">Your book</h1>
      {data ? (
        <>
          <div className="book">
            <span className={`big ${tone(data.day_change_pct)}`}>{pct(data.day_change_pct, true, 2)}</span>
            <span className="small ink2">{live ? "today so far (live)" : "at the last close"}</span>
          </div>
          {contributors.length > 0 && (
            <p className="small ink2" style={{ marginTop: -4 }}>
              Biggest effect: {contributors.map((c, i) => (
                <span key={c.sym}>
                  {i > 0 && (i === contributors.length - 1 ? " and " : ", ")}
                  {c.sym} <span className={tone(c.bps)}>{c.bps > 0 ? "+" : "−"}{Math.abs(c.bps).toFixed(0)} bps</span>
                </span>
              ))}
            </p>
          )}
        </>
      ) : (
        !error && <Skel />
      )}
      {error && <div className="notice err">{error}</div>}
      <div className="sortrow" role="group" aria-label="Sort holdings">
        {(["weight", "move", "volume"] as Sort[]).map((s) => (
          <button key={s} className="chip" aria-pressed={sort === s} onClick={() => setSort(s)}>
            {s === "weight" ? "By weight" : s === "move" ? "Biggest moves" : "Unusual volume"}
          </button>
        ))}
      </div>
      <div className="rows">
        {rows.map((h) => (
          <button className="row" key={h.symbol} onClick={() => ui.sheet(<CompanySheet h={h} />, `${h.name} details`)}>
            <div style={{ minWidth: 0 }}>
              <div className="row-sym">{h.symbol} <span className="tiny muted" style={{ fontStretch: "100%", fontWeight: 500 }}>{h.weight}%</span></div>
              <div className="row-name">{h.name}{h.vol_ratio && h.vol_ratio >= prefs.vol_x ? ` · ${h.vol_ratio.toFixed(1)}× volume` : ""}</div>
              {h.range_pos !== null && h.range_pos !== undefined && (
                <div className="range" title={`52-week range ${rupees(h.low_52w)} to ${rupees(h.high_52w)}`} aria-label={`At ${h.range_pos}% of its 52-week range`}>
                  <i style={{ left: `calc(${h.range_pos}% - 1px)` }} />
                </div>
              )}
            </div>
            <Spark values={h.spark || []} width={74} height={30} />
            <div className="row-px">
              {h.error ? (
                <span className="tiny muted">no price</span>
              ) : (
                <>
                  <div className="p">{rupees(h.price)}</div>
                  <div className={`c ${tone(h.change_pct)}`}>{pct(h.change_pct)}</div>
                </>
              )}
            </div>
          </button>
        ))}
      </div>
      <p className="tiny muted" style={{ marginTop: 10 }}>
        Mock allocation for the demo. Sparklines show the last 60 sessions; the tick marks where the price sits in its 52-week range. Edit holdings under Watch.
      </p>
    </div>
  );
}

function CompanySheet({ h }: { h: Holding }) {
  const [bars, setBars] = useState<{ date: string; close: number }[] | null>(null);
  const [recent, setRecent] = useState<Digest | null>(null);
  const ui = useUI();
  useEffect(() => {
    api.prices(h.symbol).then((p) => setBars(p.bars)).catch(() => setBars([]));
    api
      .digest({ hours: 24 * 30, price_pct: 99, vol_x: 99, lookback: 20, always: [], symbols: [h.symbol] })
      .then(setRecent)
      .catch(() => setRecent(null));
  }, [h.symbol]);
  const filings = recent ? [...recent.top, ...recent.notable].filter((x) => x.type === "filing") : [];
  return (
    <div>
      <div className="co-sym">{h.symbol}</div>
      <div className="co-name">{h.name}</div>
      <div className="co-meta">
        <span>{h.sector}</span>
        <span>{h.weight}% of your book</span>
        {h.ret_1m !== null && h.ret_1m !== undefined && <span>1 month {pct(h.ret_1m)}</span>}
        {h.ret_1y !== null && h.ret_1y !== undefined && <span>1 year {pct(h.ret_1y)}</span>}
      </div>
      {bars === null ? <Skel /> : bars.length > 1 ? (
        <Line title="Price, last 12 months" points={bars.map((b) => ({ label: new Date(b.date + "T12:00:00+05:30").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" }), value: b.close }))} format={(v) => rupees(v)} />
      ) : (
        <p className="small muted">No price history available.</p>
      )}
      <h3 className="h-sec">What it filed in the last 30 days</h3>
      {!recent ? <Skel /> : filings.length === 0 ? (
        <p className="small muted">Only routine paperwork ({recent.counts.routine} filings).</p>
      ) : (
        <ul className="fold-list">
          {filings.slice(0, 8).map((f) => f.type === "filing" && (
            <li key={f.id}>
              <span className="tiny muted">{when(f.time)}</span>
              <span>
                <b>{f.category_label}</b> · {f.pdf_url ? <a href={f.pdf_url} target="_blank" rel="noreferrer">{f.detail || f.subject}</a> : f.detail || f.subject}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="btnrow">
        <button className="btn primary" onClick={() => { ui.closeSheet(); go("research", h.symbol); }}>Research {h.symbol}</button>
      </div>
    </div>
  );
}
