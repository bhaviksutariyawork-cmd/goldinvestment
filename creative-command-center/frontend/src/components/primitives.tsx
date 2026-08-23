import type { ReactNode } from "react";
import type { Severity, Status } from "../lib/types";
import { money, pct, ratio } from "../lib/format";

/** A verdict, always with its word. Colour never carries the meaning alone. */
export function StatusPill({ status, title }: { status: Status; title?: string }) {
  return (
    <span className={`pill ${status.toLowerCase()}`} title={title}>
      <span className="dot" aria-hidden="true" />
      {status}
    </span>
  );
}

export function SeverityDot({ severity }: { severity: Severity }) {
  return <span className={`sev-${severity}`}><span className="marker" /></span>;
}

/** Rank movement. Both ranks come from equal-length windows clear of the
 *  settling lag, so a stable creative reads as flat rather than jittering. */
export function RankMovement({ movement }: { movement: number | null }) {
  if (movement === null) return <span className="muted">new</span>;
  if (movement === 0) return <span className="delta flat">—</span>;
  const up = movement > 0;
  return (
    <span className={`delta ${up ? "up" : "down"}`}>
      {up ? "▲" : "▼"} {Math.abs(movement)}
    </span>
  );
}

export function Tile({
  label, value, foot, tone,
}: { label: string; value: ReactNode; foot?: ReactNode; tone?: Severity }) {
  return (
    <div className={`tile ${tone ?? ""}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {foot ? <div className="foot">{foot}</div> : null}
    </div>
  );
}

export function Panel({
  title, note, right, children,
}: { title: string; note?: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel">
      <header>
        <div>
          <h3>{title}</h3>
          {note ? <div className="note">{note}</div> : null}
        </div>
        {right}
      </header>
      <div className="body">{children}</div>
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  return <div className="error">{error instanceof Error ? error.message : String(error)}</div>;
}

/** The three days Meta has not finished counting. Every screen that shows a
 *  window says so, because a reader who treats them as final sees a cliff
 *  that is not there. */
export function SettlingNote({ window, settling }: {
  window?: { start: string; end: string; days: number } | null;
  settling?: { start: string; end: string } | null;
}) {
  if (!window) return null;
  return (
    <span className="note">
      {window.days}d window {window.start} → {window.end}
      {settling ? ` · ${settling.start}–${settling.end} excluded as settling` : ""}
    </span>
  );
}

export function MetricCells({ metrics, currency, target }: {
  metrics: {
    spend: number; roas: number | null; cpa: number | null; purchases: number;
    frequency: number | null; cpm: number | null; outbound_ctr: number | null;
    lpv_transfer: number | null; frequency_is_lower_bound?: boolean;
  };
  currency: string;
  target?: number | null;
}) {
  const vsTarget = target && metrics.roas !== null ? metrics.roas / target : null;
  return (
    <>
      <td>{money(metrics.spend, currency)}</td>
      <td title={vsTarget ? `${ratio(vsTarget)}x target` : undefined}>{ratio(metrics.roas)}</td>
      <td>{money(metrics.cpa, currency)}</td>
      <td>{Math.round(metrics.purchases)}</td>
      <td
        className={metrics.frequency_is_lower_bound ? "muted" : undefined}
        title={
          metrics.frequency_is_lower_bound
            ? "Lower bound — no deduplicated reach for this window, so the true frequency is at least this."
            : undefined
        }
      >
        {ratio(metrics.frequency)}
        {metrics.frequency_is_lower_bound ? "+" : ""}
      </td>
      <td>{money(metrics.cpm, currency)}</td>
      <td>{pct(metrics.outbound_ctr, 2)}</td>
      <td>{pct(metrics.lpv_transfer)}</td>
    </>
  );
}

export const METRIC_HEADERS = [
  { key: "spend", label: "Spend" },
  { key: "roas", label: "ROAS" },
  { key: "cpa", label: "CPA" },
  { key: "purchases", label: "Purch" },
  { key: "frequency", label: "Freq" },
  { key: "cpm", label: "CPM" },
  { key: "outbound_ctr", label: "Out CTR" },
  { key: "lpv_transfer", label: "LPV %" },
];
