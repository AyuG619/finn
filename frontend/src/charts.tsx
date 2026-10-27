// Hand-built SVG charts: thin marks, recessive axes, a hover/tap readout, and a table twin.
import { useMemo, useRef, useState, type PointerEvent as RPE, type ReactNode } from "react";

function useTip() {
  const [tip, setTip] = useState<{ x: number; y: number; content: ReactNode } | null>(null);
  return { tip, setTip };
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return step * p;
}

function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 100000) return `${(v / 100000).toFixed(a >= 1000000 ? 0 : 1)}L`;
  if (a >= 1000) return `${(v / 1000).toFixed(a >= 10000 ? 0 : 1)}K`;
  return `${Math.round(v * 10) / 10}`;
}

// ---------------------------------------------------------------- sparkline
export function Spark({ values, width = 96, height = 30, label }: { values: number[]; width?: number; height?: number; label?: string }) {
  const pts = values.filter((v) => typeof v === "number" && isFinite(v));
  if (pts.length < 2) return <svg width={width} height={height} aria-hidden="true" />;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const x = (i: number) => 2 + (i / (pts.length - 1)) * (width - 6);
  const y = (v: number) => 3 + (1 - (v - min) / span) * (height - 6);
  const d = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const up = pts[pts.length - 1] >= pts[0];
  const color = up ? "var(--green)" : "var(--red)";
  const change = ((pts[pts.length - 1] / pts[0] - 1) * 100).toFixed(1);
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label || `Price trend ${change}% over the period`}>
      <path d={d} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1])} r={2.6} fill={color} stroke="var(--sheet)" strokeWidth={1.5} />
    </svg>
  );
}

// ---------------------------------------------------------------- columns (single series, optional polarity)
export interface BarDatum {
  label: string;
  value: number | null;
}

export function Columns({
  data,
  format,
  polar = false,
  goodWhen = "up",
  height = 150,
  title,
}: {
  data: BarDatum[];
  format: (v: number) => string;
  polar?: boolean;
  goodWhen?: "up" | "down" | "none";
  height?: number;
  title: string;
}) {
  const [table, setTable] = useState(false);
  const { tip, setTip } = useTip();
  const ref = useRef<HTMLDivElement>(null);
  const W = 340;
  const padL = 34, padR = 6, padT = 16, padB = 22;
  const vals = data.map((d) => d.value ?? 0);
  const hasPos = vals.some((v) => v > 0);
  const hasNeg = vals.some((v) => v < 0);
  const hi = hasPos ? niceMax(Math.max(...vals)) : 0;
  const lo = polar && hasNeg ? -niceMax(Math.max(...vals.map((v) => -v))) : 0;
  if (hi === 0 && lo === 0) return null;
  const plotH = height - padT - padB;
  const y = (v: number) => padT + (1 - (v - lo) / (hi - lo || 1)) * plotH;
  const band = (W - padL - padR) / Math.max(1, data.length);
  const bw = Math.min(24, band * 0.62);
  const ticks = lo < 0 ? (hi > 0 ? [lo, 0, hi] : [lo, lo / 2, 0]) : [0, hi / 2, hi];
  const lastIdx = data.length - 1;
  const extreme = vals.reduce((best, v, i) => (Math.abs(v) > Math.abs(vals[best]) ? i : best), 0);

  if (table) {
    return (
      <div>
        <div className="chart-title">{title}</div>
        <table className="tbl">
          <tbody>
            {data.map((d) => (
              <tr key={d.label}>
                <td>{d.label}</td>
                <td>{d.value === null ? "–" : format(d.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <button className="btn quiet tabletoggle" onClick={() => setTable(false)}>Show chart</button>
      </div>
    );
  }
  return (
    <div className="chart" ref={ref}>
      <div className="chart-title">{title}</div>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`${title}: ${data.map((d) => `${d.label} ${d.value === null ? "n/a" : format(d.value)}`).join(", ")}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} />
            <text className="axis" x={padL - 6} y={y(t) + 3.5} textAnchor="end">{compact(t)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          if (d.value === null) return null;
          const cx = padL + band * i + band / 2;
          const v = d.value;
          const top = y(Math.max(v, 0));
          const bot = y(Math.min(v, 0));
          const h = Math.max(1, bot - top);
          const good = goodWhen === "up" ? v >= 0 : v <= 0;
          const color = polar && goodWhen !== "none" ? (good ? "var(--green)" : "var(--red)") : "var(--s1)";
          const r = Math.min(4, bw / 2, h);
          // 4px rounded data-end, square at the baseline
          const path = v >= 0
            ? `M${cx - bw / 2},${bot} V${top + r} Q${cx - bw / 2},${top} ${cx - bw / 2 + r},${top} H${cx + bw / 2 - r} Q${cx + bw / 2},${top} ${cx + bw / 2},${top + r} V${bot} Z`
            : `M${cx - bw / 2},${top} V${bot - r} Q${cx - bw / 2},${bot} ${cx - bw / 2 + r},${bot} H${cx + bw / 2 - r} Q${cx + bw / 2},${bot} ${cx + bw / 2},${bot - r} V${top} Z`;
          const showLabel = i === lastIdx || i === extreme;
          return (
            <g key={d.label}>
              <path d={path} fill={color} />
              {showLabel && (
                <text className="lbl" x={cx} y={v >= 0 ? top - 4 : bot + 12} textAnchor="middle">{format(v)}</text>
              )}
              <text className="axis" x={cx} y={height - 6} textAnchor="middle">{d.label.replace(/^Q(\d) FY/, "Q$1·").replace(/^FY/, "FY")}</text>
              <rect
                className="hit"
                x={cx - band / 2}
                y={padT}
                width={band}
                height={plotH}
                tabIndex={0}
                onPointerEnter={(e: RPE) => setTip({ x: ((cx / W) * (ref.current?.clientWidth || W)), y: (top / height) * (ref.current?.clientHeight || height), content: <><b>{format(v)}</b> {d.label}</> })}
                onPointerLeave={() => setTip(null)}
                onFocus={() => setTip({ x: (cx / W) * (ref.current?.clientWidth || W), y: (top / height) * (ref.current?.clientHeight || height), content: <><b>{format(v)}</b> {d.label}</> })}
                onBlur={() => setTip(null)}
              />
            </g>
          );
        })}
        <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} stroke="var(--rule)" strokeWidth={1} />
      </svg>
      {tip && <div className="tip" style={{ left: tip.x, top: tip.y + 22 }}>{tip.content}</div>}
      <button className="btn quiet tabletoggle" onClick={() => setTable(true)}>View as table</button>
    </div>
  );
}

// ---------------------------------------------------------------- paired columns (two series, e.g. cash vs profit)
export function PairColumns({
  data,
  names,
  format,
  height = 160,
  title,
}: {
  data: { label: string; a: number | null; b: number | null }[];
  names: [string, string];
  format: (v: number) => string;
  height?: number;
  title: string;
}) {
  const [table, setTable] = useState(false);
  const { tip, setTip } = useTip();
  const ref = useRef<HTMLDivElement>(null);
  const W = 340, padL = 38, padR = 6, padT = 12, padB = 22;
  const all = data.flatMap((d) => [d.a ?? 0, d.b ?? 0]);
  const hi = niceMax(Math.max(0, ...all));
  const lo = -niceMax(Math.max(0, ...all.map((v) => -v)));
  const lo2 = Math.min(0, lo === -1 ? 0 : lo);
  const plotH = height - padT - padB;
  const y = (v: number) => padT + (1 - (v - lo2) / (hi - lo2 || 1)) * plotH;
  const band = (W - padL - padR) / Math.max(1, data.length);
  const bw = Math.min(14, band * 0.32);
  if (table) {
    return (
      <div>
        <div className="chart-title">{title}</div>
        <table className="tbl">
          <thead><tr><th>Year</th><th>{names[0]}</th><th>{names[1]}</th></tr></thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.label}><td>{d.label}</td><td>{d.a === null ? "–" : format(d.a)}</td><td>{d.b === null ? "–" : format(d.b)}</td></tr>
            ))}
          </tbody>
        </table>
        <button className="btn quiet tabletoggle" onClick={() => setTable(false)}>Show chart</button>
      </div>
    );
  }
  const bar = (cx: number, v: number, color: string, key: string) => {
    const top = y(Math.max(v, 0));
    const bot = y(Math.min(v, 0));
    const h = Math.max(1, bot - top);
    const r = Math.min(3, bw / 2, h);
    const p = v >= 0
      ? `M${cx - bw / 2},${bot} V${top + r} Q${cx - bw / 2},${top} ${cx - bw / 2 + r},${top} H${cx + bw / 2 - r} Q${cx + bw / 2},${top} ${cx + bw / 2},${top + r} V${bot} Z`
      : `M${cx - bw / 2},${top} V${bot - r} Q${cx - bw / 2},${bot} ${cx - bw / 2 + r},${bot} H${cx + bw / 2 - r} Q${cx + bw / 2},${bot} ${cx + bw / 2},${bot - r} V${top} Z`;
    return <path key={key} d={p} fill={color} />;
  };
  return (
    <div className="chart" ref={ref}>
      <div className="chart-title">{title}</div>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`${title}. ${data.map((d) => `${d.label}: ${names[0]} ${d.a === null ? "n/a" : format(d.a)}, ${names[1]} ${d.b === null ? "n/a" : format(d.b)}`).join("; ")}`}>
        {[lo2, 0, hi].filter((v, i, arr) => arr.indexOf(v) === i).map((t) => (
          <g key={t}>
            <line className="grid" x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} />
            <text className="axis" x={padL - 6} y={y(t) + 3.5} textAnchor="end">{compact(t)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const cx = padL + band * i + band / 2;
          return (
            <g key={d.label}>
              {d.a !== null && bar(cx - bw / 2 - 1, d.a, "var(--s1)", "a")}
              {d.b !== null && bar(cx + bw / 2 + 1, d.b, "var(--s2)", "b")}
              <text className="axis" x={cx} y={height - 6} textAnchor="middle">{d.label}</text>
              <rect className="hit" x={cx - band / 2} y={padT} width={band} height={plotH} tabIndex={0}
                onPointerEnter={() => setTip({ x: (cx / W) * (ref.current?.clientWidth || W), y: 10, content: <>{d.label} · <b>{d.a === null ? "–" : format(d.a)}</b> {names[0]} · <b>{d.b === null ? "–" : format(d.b)}</b> {names[1]}</> })}
                onPointerLeave={() => setTip(null)}
                onFocus={() => setTip({ x: (cx / W) * (ref.current?.clientWidth || W), y: 10, content: <>{d.label} · <b>{d.a === null ? "–" : format(d.a)}</b> {names[0]} · <b>{d.b === null ? "–" : format(d.b)}</b> {names[1]}</> })}
                onBlur={() => setTip(null)} />
            </g>
          );
        })}
        <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} stroke="var(--rule)" strokeWidth={1} />
      </svg>
      {tip && <div className="tip" style={{ left: Math.min(Math.max(tip.x, 90), (ref.current?.clientWidth || 300) - 90), top: tip.y + 30 }}>{tip.content}</div>}
      <div className="legend"><span><i style={{ background: "var(--s1)" }} />{names[0]}</span><span><i style={{ background: "var(--s2)" }} />{names[1]}</span></div>
      <button className="btn quiet tabletoggle" onClick={() => setTable(true)}>View as table</button>
    </div>
  );
}

// ---------------------------------------------------------------- line with crosshair (single series)
export function Line({ points, format, height = 170, title }: { points: { label: string; value: number }[]; format: (v: number) => string; height?: number; title: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const W = 340, padL = 40, padR = 8, padT = 10, padB = 20;
  const { min, max } = useMemo(() => {
    const vs = points.map((p) => p.value);
    const mn = Math.min(...vs), mx = Math.max(...vs);
    const pad = (mx - mn) * 0.08 || mx * 0.05 || 1;
    return { min: mn - pad, max: mx + pad };
  }, [points]);
  if (points.length < 2) return null;
  const x = (i: number) => padL + (i / (points.length - 1)) * (W - padL - padR);
  const y = (v: number) => padT + (1 - (v - min) / (max - min || 1)) * (height - padT - padB);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const up = points[points.length - 1].value >= points[0].value;
  const color = up ? "var(--green)" : "var(--red)";
  const ticks = [min + (max - min) * 0.1, (min + max) / 2, max - (max - min) * 0.1];
  const onMove = (e: RPE<SVGSVGElement>) => {
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - padL) / (W - padL - padR)) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };
  const hp = hover !== null ? points[hover] : null;
  const first = points[0].label, last = points[points.length - 1].label;
  return (
    <div className="chart" ref={ref}>
      <div className="chart-title">{title}</div>
      <svg viewBox={`0 0 ${W} ${height}`} onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={() => setHover(null)} role="img"
        aria-label={`${title}: from ${format(points[0].value)} on ${first} to ${format(points[points.length - 1].value)} on ${last}`}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line className="grid" x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} />
            <text className="axis" x={padL - 6} y={y(t) + 3.5} textAnchor="end">{compact(t)}</text>
          </g>
        ))}
        <path d={`${d} L${x(points.length - 1)},${height - padB} L${x(0)},${height - padB} Z`} fill={color} opacity={0.08} />
        <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={x(points.length - 1)} cy={y(points[points.length - 1].value)} r={4} fill={color} stroke="var(--sheet)" strokeWidth={2} />
        <text className="axis" x={padL} y={height - 4}>{first}</text>
        <text className="axis" x={W - padR} y={height - 4} textAnchor="end">{last}</text>
        {hp && hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={padT} y2={height - padB} stroke="var(--ink-3)" strokeWidth={1} />
            <circle cx={x(hover)} cy={y(hp.value)} r={4} fill={color} stroke="var(--sheet)" strokeWidth={2} />
          </g>
        )}
      </svg>
      {hp && hover !== null && (
        <div className="tip" style={{ left: Math.min(Math.max((x(hover) / W) * (ref.current?.clientWidth || W), 60), (ref.current?.clientWidth || 300) - 60), top: 28 }}>
          <b>{format(hp.value)}</b> {hp.label}
        </div>
      )}
    </div>
  );
}
