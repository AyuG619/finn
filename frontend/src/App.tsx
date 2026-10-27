import { useEffect, useState } from "react";
import { api, type Config } from "./api";
import AskBar from "./AskBar";
import { marketStatus } from "./format";
import { IBook, IBrief, IResearch, IWatch, Wordmark } from "./icons";
import { go, useRoute, type Route } from "./router";
import { read, write } from "./store";
import { UIProvider } from "./ui";
import Book from "./views/Book";
import Brief from "./views/Brief";
import Research from "./views/Research";
import Watch from "./views/Watch";

const TABS: { id: Route["tab"]; label: string; Icon: typeof IBrief }[] = [
  { id: "brief", label: "Brief", Icon: IBrief },
  { id: "book", label: "Book", Icon: IBook },
  { id: "research", label: "Research", Icon: IResearch },
  { id: "watch", label: "Watch", Icon: IWatch },
];

export default function App() {
  const route = useRoute();
  const [config, setConfig] = useState<Config | null>(() => read<Config | null>("finn.config", null));
  const [scrolled, setScrolled] = useState(false);
  const [market, setMarket] = useState(marketStatus());

  useEffect(() => {
    api.config().then((c) => { setConfig(c); write("finn.config", c); }).catch(() => {});
    const t = window.setInterval(() => setMarket(marketStatus()), 60000);
    const onScroll = () => setScrolled(window.scrollY > 4);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.clearInterval(t);
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  // Deep link from a push notification: /?item=ID
  const focusId = new URLSearchParams(window.location.search).get("item") || route.params.get("item") || undefined;

  useEffect(() => {
    const titles = { brief: "Brief", book: "Book", research: route.arg ? `${route.arg} research` : "Research", watch: "Watch" };
    document.title = `FINN · ${titles[route.tab]}`;
  }, [route.tab, route.arg]);

  return (
    <UIProvider>
      <div className="app">
        <header className={`topbar${scrolled ? " scrolled" : ""}`}>
          <button className="btn quiet" style={{ padding: 0, color: "var(--ink)" }} onClick={() => go("brief")} aria-label="FINN home"><Wordmark /></button>
          <span className="market"><span className={`dot${market.open ? " open" : ""}`} aria-hidden="true" />{market.label}</span>
        </header>
        <main>
          {route.tab === "brief" && <Brief focusId={focusId} />}
          {route.tab === "book" && <Book />}
          {route.tab === "research" && <Research symbol={route.arg} config={config} />}
          {route.tab === "watch" && <Watch config={config} />}
        </main>
      </div>
      <AskBar route={route} />
      <nav className="tabbar" aria-label="Main">
        <div className="tabbar-inner">
          {TABS.map(({ id, label, Icon }) => (
            <button key={id} className="tab" aria-current={route.tab === id ? "page" : undefined} onClick={() => go(id)}>
              <span className="tab-icon"><Icon /></span>
              {label}
            </button>
          ))}
        </div>
      </nav>
    </UIProvider>
  );
}
