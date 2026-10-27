// Tiny hash router: #/brief, #/book, #/research/PERSISTENT, #/watch
import { useEffect, useState } from "react";

export type Route = { tab: "brief" | "book" | "research" | "watch"; arg?: string; params: URLSearchParams };

export function parse(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const [tab, arg] = path.split("/");
  const params = new URLSearchParams(query);
  if (tab === "book" || tab === "research" || tab === "watch") return { tab, arg: arg ? decodeURIComponent(arg) : undefined, params };
  return { tab: "brief", params };
}

export function go(tab: Route["tab"], arg?: string, params?: Record<string, string>) {
  const q = params ? "?" + new URLSearchParams(params).toString() : "";
  const next = `#/${tab}${arg ? "/" + encodeURIComponent(arg) : ""}${q}`;
  if (window.location.hash !== next) window.location.hash = next;
  window.scrollTo({ top: 0 });
}

export function useRoute(): Route {
  const [r, setR] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const on = () => setR(parse(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return r;
}
