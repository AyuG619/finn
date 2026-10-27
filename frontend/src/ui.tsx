import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { IClose } from "./icons";

interface UI {
  toast: (msg: string) => void;
  sheet: (content: ReactNode, label: string) => void;
  closeSheet: () => void;
}

const Ctx = createContext<UI>({ toast: () => {}, sheet: () => {}, closeSheet: () => {} });
export const useUI = () => useContext(Ctx);

export function UIProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<string | null>(null);
  const [sheet, setSheet] = useState<{ content: ReactNode; label: string } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const lastFocus = useRef<HTMLElement | null>(null);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), 3200);
  }, []);
  const openSheet = useCallback((content: ReactNode, label: string) => {
    lastFocus.current = document.activeElement as HTMLElement | null;
    setSheet({ content, label });
  }, []);
  const closeSheet = useCallback(() => {
    setSheet(null);
    lastFocus.current?.focus?.();
  }, []);

  useEffect(() => {
    if (!sheet) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeSheet();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [sheet, closeSheet]);

  return (
    <Ctx.Provider value={{ toast: showToast, sheet: openSheet, closeSheet }}>
      {children}
      {sheet && (
        <>
          <div className="scrim" onClick={closeSheet} />
          <div className="sheet" role="dialog" aria-modal="true" aria-label={sheet.label}>
            <div className="sheet-grip" />
            <button className="iconbtn sheet-close" onClick={closeSheet} aria-label="Close">
              <IClose />
            </button>
            {sheet.content}
          </div>
        </>
      )}
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </Ctx.Provider>
  );
}

export function Skel() {
  return (
    <div className="skel" aria-hidden="true">
      <i />
      <i />
      <i />
    </div>
  );
}
