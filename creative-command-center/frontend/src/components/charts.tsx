import { useMemo, useState, type ReactNode } from "react";
import { money, pct, ratio, shortDate } from "../lib/format";

/* Colour scales.

   Delivery share is coloured by ROAS against its target — a polarity, so a
   diverging scale: two hues with a neutral at parity, equal steps per arm.
   Every scale below was checked with the palette validator against the dark
   chart surface (#1a1a19). */

const DIVERGING = {
  farAbove: "#6da7ec",
  above: "#256abf",
  neutral: "#383835",
  below: "#e88a8a",
  farBelow: "#b03636",
};

const SEQUENTIAL = ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"];
// The two lightest steps need dark ink; white on #9ec5f4 is unreadable.
const SEQUENTIAL_INK = ["#ffffff", "#ffffff", "#ffffff", "#0d0d0d", "#0d0d0d"];

export const SERIES = { spend: "#3987e5", roas: "#d95926", purchases: "#199e70" };

export function roasColor(roas: number | null, target: number | null): string {
  if (roas === null) return "var(--surface-3)";
  if (!target) {
    // No target for this band. Neutral rather than a guess — a colour here
    // would be an opinion the data does not support.
    return DIVERGING.neutral;
  }
  const index = roas / target;
  if (index >= 1.5) return DIVERGING.farAbove;
  if (index >= 1.0) return DIVERGING.above;
  if (index >= 0.7) return DIVERGING.neutral;
  if (index >= 0.4) return DIVERGING.below;
  return DIVERGING.farBelow;
}

function coverageStep(impressions: number, max: number): number {
  if (max <= 0) return 0;
  const step = Math.floor(
    (Math.log10(impressions + 1) / Math.log10(max + 1)) * SEQUENTIAL.length,
  );
  return Math.max(0, Math.min(SEQUENTIAL.length - 1, step));
}

export function coverageColor(impressions: number, max: number): string {
  return SEQUENTIAL[coverageStep(impressions, max)];
}

export function coverageInk(impressions: number, max: number): string {
  return SEQUENTIAL_INK[coverageStep(impressions, max)];
}

export function RoasLegend({ target }: { target: number | null }) {
  const keys: [string, string][] = [
    [DIVERGING.farAbove, target ? `≥ ${(target * 1.5).toFixed(1)} ROAS` : "well above target"],
    [DIVERGING.above, target ? `${target.toFixed(1)}–${(target * 1.5).toFixed(1)}` : "above target"],
    [DIVERGING.neutral, target ? `${(target * 0.7).toFixed(1)}–${target.toFixed(1)}` : "near target"],
    [DIVERGING.below, target ? `${(target * 0.4).toFixed(1)}–${(target * 0.7).toFixed(1)}` : "below"],
    [DIVERGING.farBelow, target ? `< ${(target * 0.4).toFixed(1)}` : "far below"],
  ];
  return (
    <div className="legend">
      <span className="muted">ROAS vs target:</span>
      {keys.map(([colour, label]) => (
        <span className="key" key={label}>
          <span className="swatch" style={{ background: colour, border: "1px solid var(--border)" }} />
          {label}
        </span>
      ))}
    </div>
  );
}

/* --- tooltip ------------------------------------------------------------ */

interface TipState { x: number; y: number; content: ReactNode }

export function useTooltip() {
  const [tip, setTip] = useState<TipState | null>(null);
  const show = (event: { clientX: number; clientY: number }, content: ReactNode) =>
    setTip({ x: event.clientX, y: event.clientY, content });
  const hide = () => setTip(null);
  const node = tip ? (
    <div
      className="tooltip"
      style={{
        left: Math.min(tip.x + 12, window.innerWidth - 280),
        top: Math.min(tip.y + 12, window.innerHeight - 140),
      }}
      role="tooltip"
    >
      {tip.content}
    </div>
  ) : null;
  return { show, hide, node };
}

export function TipRows({ title, rows }: { title: string; rows: [string, ReactNode][] }) {
  return (
    <>
      <div className="t-title">{title}</div>
      {rows.map(([key, value]) => (
        <div className="t-row" key={key}>
          <span className="k">{key}</span>
          <span>{value}</span>
        </div>
      ))}
    </>
  );
}

/* --- delivery bar ------------------------------------------------------- */

export interface Segment {
  id: string;
  label: string;
  share: number;
  roas: number | null;
  spend: number;
  purchases?: number;
  target?: number | null;
}

/** One ad set's budget, split by ad and coloured by return.
 *
 *  A wide segment in the below-target colour is the picture of intra-ad-set
 *  misallocation — the thing that shows up nowhere in Ads Manager. */
export function DeliveryBar({
  segments, currency, onSelect,
}: { segments: Segment[]; currency: string; onSelect?: (id: string) => void }) {
  const tip = useTooltip();
  return (
    <>
      <div className="delivery-bar" role="img" aria-label="Delivery share by ad, coloured by ROAS against target">
        {segments.map((segment) => (
          <div
            key={segment.id}
            className="seg"
            style={{
              flexGrow: Math.max(segment.share, 0.005),
              flexBasis: 0,
              background: roasColor(segment.roas, segment.target ?? null),
              border: "1px solid var(--border)",
              cursor: onSelect ? "pointer" : "default",
            }}
            onMouseMove={(event) =>
              tip.show(
                event,
                <TipRows
                  title={segment.label}
                  rows={[
                    ["Delivery share", pct(segment.share, 1)],
                    ["Spend", money(segment.spend, currency)],
                    ["ROAS", ratio(segment.roas)],
                    ...(segment.target ? ([["Target", ratio(segment.target)]] as [string, ReactNode][]) : []),
                    ...(segment.purchases !== undefined
                      ? ([["Purchases", String(segment.purchases)]] as [string, ReactNode][])
                      : []),
                  ]}
                />,
              )
            }
            onMouseLeave={tip.hide}
            onClick={() => onSelect?.(segment.id)}
          />
        ))}
      </div>
      {tip.node}
    </>
  );
}

/* --- trend chart -------------------------------------------------------- */

export interface TrendSeries {
  key: string;
  label: string;
  colour: string;
  values: (number | null)[];
  formatter: (value: number | null) => string;
}

/** One measure, one chart. Two measures of different scale get two charts
 *  stacked as small multiples — never a second y-axis. */
export function TrendChart({
  dates, settling, series, height = 140,
}: { dates: string[]; settling: boolean[]; series: TrendSeries; height?: number }) {
  const tip = useTooltip();
  const [hover, setHover] = useState<number | null>(null);
  // A wide viewBox keeps the chart short once it scales to the panel width.
  const width = 1200;
  const pad = { top: 12, right: 14, bottom: 18, left: 54 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const { points, max, firstSettlingIndex } = useMemo(() => {
    const clean = series.values.map((v) => (v === null || Number.isNaN(v) ? null : v));
    const top = Math.max(...clean.map((v) => v ?? 0), 0.0001);
    const step = dates.length > 1 ? plotW / (dates.length - 1) : plotW;
    return {
      points: clean.map((value, index) => ({
        x: pad.left + index * step,
        y: value === null ? null : pad.top + plotH - (value / top) * plotH,
        value,
      })),
      max: top,
      firstSettlingIndex: settling.findIndex(Boolean),
    };
  }, [dates, series.values, settling, plotH, plotW, pad.left, pad.top]);

  const path = points
    .filter((p) => p.y !== null)
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${(p.y as number).toFixed(1)}`)
    .join(" ");

  return (
    <>
      <svg
        className="chart"
        viewBox={`0 0 ${width} ${height}`}
        // Scale to the panel width and let the viewBox set the height, rather
        // than pinning a height and letterboxing the plot inside it.
        style={{ width: "100%", height: "auto", display: "block" }}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`${series.label} by day`}
        onMouseLeave={() => { setHover(null); tip.hide(); }}
        onMouseMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const relative = ((event.clientX - box.left) / box.width) * width;
          const step = dates.length > 1 ? plotW / (dates.length - 1) : plotW;
          const index = Math.round((relative - pad.left) / step);
          if (index < 0 || index >= dates.length) return;
          setHover(index);
          tip.show(
            event,
            <TipRows
              title={`${shortDate(dates[index])}${settling[index] ? " · settling" : ""}`}
              rows={[
                [series.label, series.formatter(points[index]?.value ?? null)],
                ...(settling[index]
                  ? ([["Note", "Attribution incomplete"]] as [string, ReactNode][])
                  : []),
              ]}
            />,
          );
        }}
      >
        {/* The trailing settling days, banded rather than plotted as fact. */}
        {firstSettlingIndex >= 0 ? (
          <>
            <rect
              className="settling-band"
              x={points[firstSettlingIndex].x}
              y={pad.top}
              width={width - pad.right - points[firstSettlingIndex].x}
              height={plotH}
            />
            {/* Anchored to the right edge: the band is only three days wide,
                so a left-anchored label would run off the plot. */}
            <text
              className="settling-label"
              x={width - pad.right - 3}
              y={pad.top + 10}
              textAnchor="end"
            >
              settling
            </text>
          </>
        ) : null}

        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line
              className="grid-line"
              x1={pad.left} x2={width - pad.right}
              y1={pad.top + plotH * fraction} y2={pad.top + plotH * fraction}
            />
            <text
              className="axis-label"
              x={pad.left - 6}
              y={pad.top + plotH * fraction + 3}
              textAnchor="end"
            >
              {series.formatter(max * (1 - fraction))}
            </text>
          </g>
        ))}

        <path d={path} fill="none" stroke={series.colour} strokeWidth={2}
              strokeLinejoin="round" strokeLinecap="round" />

        {hover !== null && points[hover]?.y !== null ? (
          <>
            <line className="baseline" x1={points[hover].x} x2={points[hover].x}
                  y1={pad.top} y2={pad.top + plotH} />
            <circle cx={points[hover].x} cy={points[hover].y as number} r={4}
                    fill={series.colour} stroke="var(--surface-1)" strokeWidth={2} />
          </>
        ) : null}

        <line className="baseline" x1={pad.left} x2={width - pad.right}
              y1={pad.top + plotH} y2={pad.top + plotH} />
        {dates.length ? (
          <>
            <text className="axis-label" x={pad.left} y={height - 5}>{shortDate(dates[0])}</text>
            <text className="axis-label" x={width - pad.right} y={height - 5} textAnchor="end">
              {shortDate(dates[dates.length - 1])}
            </text>
          </>
        ) : null}
      </svg>
      {tip.node}
    </>
  );
}

/* --- budget pacing ------------------------------------------------------ */

export function PacingChart({
  days, currency,
}: {
  days: { date: string; spend: number; daily_budget: number | null; pacing: number | null }[];
  currency: string;
}) {
  const tip = useTooltip();
  const width = 520;
  const height = 96;
  const pad = { top: 8, right: 10, bottom: 18, left: 46 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const max = Math.max(...days.map((d) => Math.max(d.spend, d.daily_budget ?? 0)), 1);
  const slot = plotW / Math.max(days.length, 1);

  return (
    <>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`}
           style={{ width: "100%", height: "auto", display: "block" }}
           role="img" aria-label="Daily spend against the ad set's daily budget">
        {[0, 1].map((fraction) => (
          <g key={fraction}>
            <line className="grid-line" x1={pad.left} x2={width - pad.right}
                  y1={pad.top + plotH * fraction} y2={pad.top + plotH * fraction} />
            <text className="axis-label" x={pad.left - 6} y={pad.top + plotH * fraction + 3}
                  textAnchor="end">
              {money(max * (1 - fraction), currency)}
            </text>
          </g>
        ))}

        {days.map((day, index) => {
          const barHeight = (day.spend / max) * plotH;
          const x = pad.left + index * slot + 3;
          const budgetY = day.daily_budget
            ? pad.top + plotH - (day.daily_budget / max) * plotH
            : null;
          return (
            <g key={day.date}
               onMouseMove={(event) =>
                 tip.show(event, <TipRows title={shortDate(day.date)} rows={[
                   ["Spend", money(day.spend, currency)],
                   ["Daily budget", money(day.daily_budget, currency)],
                   ["Pacing", pct(day.pacing)],
                 ]} />)}
               onMouseLeave={tip.hide}>
              <rect x={x} y={pad.top + plotH - barHeight} width={Math.max(slot - 6, 3)}
                    height={Math.max(barHeight, 1)} rx={3} fill={SERIES.spend} />
              {budgetY !== null ? (
                <line x1={x - 2} x2={x + slot - 4} y1={budgetY} y2={budgetY}
                      stroke="var(--text-muted)" strokeWidth={2} strokeDasharray="4 3" />
              ) : null}
            </g>
          );
        })}
        <line className="baseline" x1={pad.left} x2={width - pad.right}
              y1={pad.top + plotH} y2={pad.top + plotH} />
      </svg>
      <div className="legend">
        <span className="key">
          <span className="swatch" style={{ background: SERIES.spend }} /> daily spend
        </span>
        <span className="key">
          <span className="swatch" style={{ background: "var(--text-muted)", height: 2 }} /> daily budget
        </span>
      </div>
      {tip.node}
    </>
  );
}

/* --- sparkline ---------------------------------------------------------- */

export function Sparkline({
  values, colour = SERIES.spend, settlingFrom,
}: { values: (number | null)[]; colour?: string; settlingFrom?: number }) {
  const width = 84;
  const height = 20;
  const clean = values.map((v) => v ?? 0);
  const max = Math.max(...clean, 0.0001);
  const step = clean.length > 1 ? width / (clean.length - 1) : width;
  const path = clean
    .map((value, index) => `${index === 0 ? "M" : "L"}${(index * step).toFixed(1)},${(height - (value / max) * height).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={width} height={height} className="chart" aria-hidden="true">
      {settlingFrom !== undefined && settlingFrom >= 0 ? (
        <rect className="settling-band" x={settlingFrom * step} y={0}
              width={width - settlingFrom * step} height={height} />
      ) : null}
      <path d={path} fill="none" stroke={colour} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}
