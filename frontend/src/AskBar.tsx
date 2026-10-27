// Chat is a command line, not a destination: one bar, everywhere, that either moves you to the
// right place ("research trent", "show last week", "alert me on 3% moves") or answers a question
// about the company you're looking at, from its own documents.
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { IMic, ISend } from "./icons";
import { go, type Route } from "./router";
import { setPrefs } from "./store";
import { useUI } from "./ui";
import { Answer } from "./views/Research";

type SR = any;

function speechCtor(): SR | null {
  const w = window as any;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export default function AskBar({ route }: { route: Route }) {
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const rec = useRef<SR | null>(null);
  const ui = useUI();
  const Speech = speechCtor();
  const inResearch = route.tab === "research" && !!route.arg;

  useEffect(() => () => rec.current?.abort?.(), []);

  const resolveCompany = async (name: string) => {
    const n = name.trim().replace(/[?.!]+$/, "");
    if (/^[A-Z0-9&-]{2,15}$/.test(n)) return go("research", n);
    try {
      const r = await api.search(n);
      if (r.results[0]) return go("research", r.results[0].symbol);
    } catch {
      /* fall through */
    }
    go("research", n.toUpperCase().replace(/\s+/g, ""));
  };

  const handle = async (raw: string) => {
    const t = raw.trim();
    if (!t) return;
    const low = t.toLowerCase();
    setText("");
    let m: RegExpMatchArray | null;
    if ((m = low.match(/(?:price|move|moves|moving|falls?|rises?|drops?)[^\d]{0,30}(\d+(?:\.\d+)?)\s*(?:%|percent)/))) {
      setPrefs({ price_pct: Math.min(20, Math.max(0.5, Number(m[1]))) });
      ui.toast(`Price alerts now trigger at ±${m[1]}% a day.`);
      return go("watch");
    }
    if ((m = low.match(/volume[^\d]{0,30}(\d+(?:\.\d+)?)\s*(?:x|×|times)/))) {
      setPrefs({ vol_x: Math.min(10, Math.max(1.5, Number(m[1]))) });
      ui.toast(`Volume alerts now trigger at ${m[1]}× usual volume.`);
      return go("watch");
    }
    if ((m = low.match(/(?:last|past)\s+(\d+)\s*(hour|hours|day|days|week|weeks|month|months)/))) {
      const n = Number(m[1]);
      const unit = m[2];
      const hours = unit.startsWith("hour") ? n : unit.startsWith("day") ? n * 24 : unit.startsWith("week") ? n * 168 : n * 720;
      setPrefs({ windowKey: String(Math.min(24 * 90, Math.max(1, hours))) });
      return go("brief");
    }
    if (/\b(this|last) week\b/.test(low)) {
      setPrefs({ windowKey: "168" });
      return go("brief");
    }
    if (/\b(today|since (i|we) last|what('s| is) new|what changed|brief)\b/.test(low) && !inResearch) {
      setPrefs({ windowKey: /since/.test(low) ? "since" : "24" });
      return go("brief");
    }
    if ((m = t.match(/^(?:research|dig(?:\s+into)?|analy[sz]e|open|show(?:\s+me)?|look\s+at|tell\s+me\s+about|pull\s+up)\s+(.+)$/i))) {
      return resolveCompany(m[1]);
    }
    if (!inResearch && /^[A-Za-z&-]{2,15}$/.test(t)) return resolveCompany(t.toUpperCase());
    if (inResearch) {
      setBusy(true);
      try {
        const r = await api.ask(route.arg!, t);
        ui.sheet(
          <div>
            <div className="tiny muted">{route.arg} · {t}</div>
            {r.answer ? <Answer text={r.answer} /> : <p>{r.note || "No answer."}</p>}
            {r.retrieved && r.retrieved.length > 0 && <p className="tiny muted">Searched: {r.retrieved.slice(0, 4).join(", ")}</p>}
          </div>,
          "Answer",
        );
      } catch (e: any) {
        ui.toast(e.message);
      } finally {
        setBusy(false);
      }
      return;
    }
    // A name in a sentence ("how is asian paints doing") -> research it.
    return resolveCompany(t.replace(/^(how is|how's|what about|and)\s+/i, "").replace(/\s+(doing|now|today)\??$/i, ""));
  };

  const listen = () => {
    if (!Speech) return;
    if (listening) {
      rec.current?.stop();
      return;
    }
    const r = new Speech();
    r.lang = "en-IN";
    r.interimResults = true;
    r.maxAlternatives = 1;
    let finalText = "";
    r.onresult = (e: any) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const s = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += s;
        else interim += s;
      }
      setText((finalText + interim).trim());
    };
    r.onerror = () => setListening(false);
    r.onend = () => {
      setListening(false);
      if (finalText.trim()) handle(finalText);
    };
    rec.current = r;
    setListening(true);
    r.start();
  };

  const placeholder = inResearch ? `Ask about ${route.arg}, or say a ticker` : "Ask FINN, or say a ticker";
  return (
    <div className="askdock">
      <form className="askbar" onSubmit={(e) => { e.preventDefault(); handle(text); }} role="search">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder={busy ? "Reading the documents…" : placeholder} aria-label="Ask FINN" enterKeyHint="go" disabled={busy} />
        {Speech && (
          <button type="button" className={`iconbtn${listening ? " listening" : ""}`} onClick={listen} aria-label={listening ? "Stop listening" : "Speak"} aria-pressed={listening}>
            <IMic />
          </button>
        )}
        <button type="submit" className="iconbtn primary" aria-label="Go" disabled={busy}><ISend /></button>
      </form>
    </div>
  );
}
