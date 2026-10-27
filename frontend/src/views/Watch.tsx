import { useEffect, useState } from "react";
import { api, type Config } from "../api";
import { num } from "../format";
import { DEFAULT_PREFS, usePrefs } from "../store";
import { useUI } from "../ui";

const WATCHABLE: [string, string][] = [
  ["results", "Results"],
  ["m_and_a", "M&A"],
  ["management_change", "Management changes"],
  ["fund_raise", "Fund raises"],
  ["litigation", "Litigation & regulatory"],
  ["pledging", "Promoter pledging"],
  ["credit_rating", "Credit ratings"],
  ["insider_trading", "Insider trades"],
  ["capex", "Capex"],
  ["bonus_split", "Bonus / split"],
  ["dividend", "Dividends"],
  ["press_release", "Orders & press releases"],
];

function Stepper({ value, onChange, step, min, max, suffix, label, digits = 1 }: { value: number; onChange: (v: number) => void; step: number; min: number; max: number; suffix: string; label: string; digits?: number }) {
  const set = (v: number) => onChange(Math.min(max, Math.max(min, Math.round(v / step) * step)));
  return (
    <span className="stepper" role="group" aria-label={label}>
      <button type="button" onClick={() => set(value - step)} aria-label={`Lower ${label}`}>−</button>
      <output aria-live="polite">{num(value, digits)}{suffix}</output>
      <button type="button" onClick={() => set(value + step)} aria-label={`Raise ${label}`}>+</button>
    </span>
  );
}

function b64ToUint8(b64: string): Uint8Array {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
const standalone = () => window.matchMedia?.("(display-mode: standalone)").matches || (navigator as any).standalone === true;

export default function Watch({ config }: { config: Config | null }) {
  const [prefs, setPrefs] = usePrefs();
  const ui = useUI();
  const [sub, setSub] = useState<PushSubscription | null>(null);
  const [pushState, setPushState] = useState<"unsupported" | "ios-install" | "off" | "on" | "denied" | "working">("off");
  const [usage, setUsage] = useState<any>(null);
  const [addQ, setAddQ] = useState("");
  const [addResults, setAddResults] = useState<{ symbol: string; name: string }[]>([]);

  useEffect(() => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      setPushState(isIOS() && !standalone() ? "ios-install" : "unsupported");
      return;
    }
    if (Notification.permission === "denied") setPushState("denied");
    navigator.serviceWorker.ready
      .then((reg) => reg.pushManager.getSubscription())
      .then((s) => {
        setSub(s);
        if (s) setPushState("on");
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    api.usage().then(setUsage).catch(() => setUsage(null));
  }, []);

  // Keep the server's copy of the alert rules in step with the phone's.
  useEffect(() => {
    if (!sub) return;
    const t = window.setTimeout(() => {
      api.subscribe(sub.toJSON(), serverPrefs()).catch(() => {});
    }, 800);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sub, prefs.price_pct, prefs.vol_x, prefs.lookback, prefs.always, prefs.symbols]);

  const serverPrefs = () => ({ price_pct: prefs.price_pct, vol_x: prefs.vol_x, lookback: prefs.lookback, always: prefs.always, symbols: prefs.symbols });

  const turnOn = async () => {
    if (!config) return;
    setPushState("working");
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") {
        setPushState(perm === "denied" ? "denied" : "off");
        return;
      }
      const reg = await navigator.serviceWorker.ready;
      let s = await reg.pushManager.getSubscription();
      if (s) await s.unsubscribe();
      s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToUint8(config.push_public_key) as unknown as BufferSource });
      await api.subscribe(s.toJSON(), serverPrefs());
      setSub(s);
      setPushState("on");
      ui.toast("Phone alerts are on.");
    } catch (e: any) {
      setPushState("off");
      ui.toast(`Couldn't turn on alerts: ${e?.message || e}`);
    }
  };
  const turnOff = async () => {
    if (!sub) return;
    const endpoint = sub.endpoint;
    await sub.unsubscribe().catch(() => {});
    await api.unsubscribe(endpoint).catch(() => {});
    setSub(null);
    setPushState("off");
    ui.toast("Phone alerts are off.");
  };
  const test = async () => {
    if (!sub) return;
    try {
      await api.pushTest(sub.endpoint);
      ui.toast("Test alert sent.");
    } catch (e: any) {
      ui.toast(e.message);
    }
  };

  useEffect(() => {
    const s = addQ.trim();
    if (s.length < 2) {
      setAddResults([]);
      return;
    }
    const t = window.setTimeout(() => api.search(s).then((r) => setAddResults(r.results)).catch(() => setAddResults([])), 250);
    return () => window.clearTimeout(t);
  }, [addQ]);

  const holdings = prefs.symbols || config?.portfolio.map((h) => h.symbol) || [];
  const names = Object.fromEntries((config?.portfolio || []).map((h) => [h.symbol, h.name]));
  const toggleCat = (c: string) => setPrefs({ always: prefs.always.includes(c) ? prefs.always.filter((x) => x !== c) : [...prefs.always, c] });

  return (
    <div className="view">
      <h1 className="h-page">What FINN watches for</h1>
      <p className="lede">You decide what “unusual” means. FINN checks every holding at each close and every filing as it lands.</p>

      <div className="rule">
        Tell me when a holding moves more than{" "}
        <Stepper label="price move threshold" value={prefs.price_pct} onChange={(v) => setPrefs({ price_pct: v })} step={0.5} min={0.5} max={20} suffix="%" />{" "}
        in a day,
      </div>
      <div className="rule">
        or trades more than{" "}
        <Stepper label="volume multiple" value={prefs.vol_x} onChange={(v) => setPrefs({ vol_x: v })} step={0.5} min={1.5} max={10} suffix="×" />{" "}
        its usual volume over the past{" "}
        <Stepper label="volume look-back" value={prefs.lookback} onChange={(v) => setPrefs({ lookback: v })} step={5} min={5} max={60} suffix=" days" digits={0} />.
      </div>
      <div className="rule" style={{ lineHeight: 1.5 }}>
        Always flag these filings, even when they look small:
        <div className="cats">
          {WATCHABLE.map(([k, l]) => (
            <button key={k} className="chip soft" aria-pressed={prefs.always.includes(k)} onClick={() => toggleCat(k)}>{l}</button>
          ))}
        </div>
      </div>
      <div className="btnrow">
        <button className="btn quiet" onClick={() => setPrefs({ price_pct: DEFAULT_PREFS.price_pct, vol_x: DEFAULT_PREFS.vol_x, lookback: DEFAULT_PREFS.lookback, always: DEFAULT_PREFS.always })}>Reset rules</button>
      </div>

      <h2 className="h-sec">Phone alerts</h2>
      <div className="card">
        {pushState === "ios-install" && <p className="small" style={{ margin: 0 }}>On iPhone, alerts need FINN on your home screen: tap Share, then Add to Home Screen, and open FINN from there.</p>}
        {pushState === "unsupported" && <p className="small" style={{ margin: 0 }}>This browser can't receive web push. Try Chrome on Android, or Safari on an iPhone with FINN added to the home screen.</p>}
        {pushState === "denied" && <p className="small" style={{ margin: 0 }}>Notifications are blocked for this site. Allow them in the browser's site settings, then come back.</p>}
        {(pushState === "off" || pushState === "working") && (
          <>
            <p className="small" style={{ marginTop: 0 }}>Get a notification when a watched filing lands or a holding crosses your thresholds. FINN checks every 15 minutes.</p>
            <button className="btn primary" onClick={turnOn} disabled={pushState === "working" || !config}>{pushState === "working" ? "Turning on…" : "Turn on phone alerts"}</button>
          </>
        )}
        {pushState === "on" && (
          <>
            <p className="small" style={{ marginTop: 0 }}>On for this device, using the rules above.</p>
            <div className="btnrow" style={{ marginTop: 0 }}>
              <button className="btn" onClick={test}>Send a test alert</button>
              <button className="btn quiet" onClick={turnOff}>Turn off</button>
            </div>
          </>
        )}
      </div>

      <h2 className="h-sec">Holdings <span className="count">{holdings.length}</span></h2>
      <div className="card">
        {holdings.map((s) => (
          <div className="holding-edit" key={s}>
            <span><b className="cond">{s}</b> <span className="small muted">{names[s] || ""}</span></span>
            <button className="btn quiet" onClick={() => setPrefs({ symbols: holdings.filter((x) => x !== s) })} aria-label={`Remove ${s}`}>Remove</button>
          </div>
        ))}
        <form className="search" onSubmit={(e) => { e.preventDefault(); const r = addResults[0]; if (r && !holdings.includes(r.symbol)) { setPrefs({ symbols: [...holdings, r.symbol] }); setAddQ(""); } }}>
          <input value={addQ} onChange={(e) => setAddQ(e.target.value)} placeholder="Add a company" aria-label="Add a company" />
        </form>
        {addResults.length > 0 && (
          <div className="suggest">
            {addResults.slice(0, 5).map((r) => (
              <button key={r.symbol} onClick={() => { if (!holdings.includes(r.symbol)) setPrefs({ symbols: [...holdings, r.symbol] }); setAddQ(""); }}>
                <b className="cond">{r.symbol}</b><span className="small muted">{r.name}</span>
              </button>
            ))}
          </div>
        )}
        {prefs.symbols && (
          <div className="btnrow">
            <button className="btn quiet" onClick={() => setPrefs({ symbols: null })}>Back to the demo portfolio</button>
          </div>
        )}
        <p className="tiny muted" style={{ marginBottom: 0 }}>A custom list is weighted equally. Any NSE company works.</p>
      </div>

      <h2 className="h-sec">Under the hood</h2>
      <div className="card small">
        {usage ? (
          <>
            <p style={{ marginTop: 0 }}>
              Since the server started: <b>{usage.llm.calls}</b> model calls, <b>{num((usage.llm.input_tokens + usage.llm.output_tokens) / 1000, 0)}k</b> tokens,{" "}
              <b>{usage.llm.cache_hits}</b> answered from cache. At list prices that would have cost about <b>${num(usage.llm.est_cost_usd_at_list_price, 3)}</b>; on free tiers it cost nothing.
            </p>
            <p className="muted" style={{ marginBottom: 0 }}>
              Cheap, fast models read filings and transcripts; a stronger one only writes the promise-vs-delivery analysis. Numbers are computed in code, never by a model.
              Models in use: {Array.from(new Set((usage.llm_status?.tiers?.fast || []).concat(usage.llm_status?.tiers?.smart || []).map((m: any) => m.model.split(":")[1]))).slice(0, 4).join(", ")}.
            </p>
          </>
        ) : (
          <p className="muted" style={{ margin: 0 }}>Usage unavailable.</p>
        )}
      </div>
    </div>
  );
}
