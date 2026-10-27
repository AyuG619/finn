import type { SVGProps } from "react";

const base = (p: SVGProps<SVGSVGElement>) => ({
  width: 22,
  height: 22,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
  ...p,
});

export const IBrief = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M7 3.5h7l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 19V5a1.5 1.5 0 0 1 1-1.5Z" />
    <path d="M14 3.5V8h4" />
    <path d="M9 12.5h6M9 16h4" />
  </svg>
);
export const IBook = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M4 19.5h16" />
    <path d="M6.5 16V11M10.5 16V7M14.5 16v-6M18.5 16V5" />
  </svg>
);
export const IResearch = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <circle cx="10.5" cy="10.5" r="6" />
    <path d="m15 15 5 5" />
    <path d="M8 10.5h5M10.5 8v5" />
  </svg>
);
export const IWatch = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2h-15Z" />
    <path d="M10 20.5a2 2 0 0 0 4 0" />
  </svg>
);
export const IMic = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base({ width: 20, height: 20, ...p })}>
    <rect x="9" y="3.5" width="6" height="11" rx="3" />
    <path d="M6 11.5a6 6 0 0 0 12 0M12 17.5v3" />
  </svg>
);
export const ISend = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base({ width: 20, height: 20, ...p })}>
    <path d="M5 12h13M13 6.5 18.5 12 13 17.5" />
  </svg>
);
export const IClose = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base({ width: 20, height: 20, ...p })}>
    <path d="m6 6 12 12M18 6 6 18" />
  </svg>
);
export const IBack = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base({ width: 20, height: 20, ...p })}>
    <path d="M15 5.5 8.5 12l6.5 6.5" />
  </svg>
);
export const IDoc = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base({ width: 16, height: 16, ...p })}>
    <path d="M7 3.5h7l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 19V5" />
    <path d="M14 3.5V8h4" />
  </svg>
);
export const ICheck = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base({ width: 16, height: 16, ...p })}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </svg>
);

export const Wordmark = () => (
  <span className="wordmark" aria-label="FINN">
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="4" fill="var(--ink)" />
      <path d="M8 8.5h8M8 12h8M8 15.5h4.5" stroke="var(--sheet)" strokeWidth="1.8" strokeLinecap="round" />
      <path d="m15.2 15.6 1.4 1.4 2.6-3" stroke="var(--green)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
    FINN
  </span>
);
