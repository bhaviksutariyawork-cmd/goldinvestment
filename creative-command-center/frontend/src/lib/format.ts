/** Display helpers. Nothing here computes a verdict — the engine already did. */

const CURRENCY_SYMBOL: Record<string, string> = { INR: "₹", USD: "$", GBP: "£", EUR: "€" };

export function money(value: number | null | undefined, currency = "INR"): string {
  if (value === null || value === undefined) return "—";
  const symbol = CURRENCY_SYMBOL[currency] ?? "";
  const abs = Math.abs(value);
  if (abs >= 1_00_00_000) return `${symbol}${(value / 1_00_00_000).toFixed(2)}Cr`;
  if (abs >= 1_00_000) return `${symbol}${(value / 1_00_000).toFixed(2)}L`;
  if (abs >= 1_000) return `${symbol}${(value / 1_000).toFixed(1)}k`;
  return `${symbol}${value.toFixed(0)}`;
}

export function moneyExact(value: number | null | undefined, currency = "INR"): string {
  if (value === null || value === undefined) return "—";
  const symbol = CURRENCY_SYMBOL[currency] ?? "";
  return `${symbol}${value.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export function ratio(value: number | null | undefined, places = 2): string {
  return value === null || value === undefined ? "—" : value.toFixed(places);
}

export function pct(value: number | null | undefined, places = 0): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(places)}%`;
}

export function count(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : Math.round(value).toLocaleString("en-IN");
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const hours = (Date.now() - then) / 36e5;
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m ago`;
  if (hours < 48) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function shortDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/** How a ROAS sits against its target. Drives the diverging colour scale on
 *  delivery bars — above target is one pole, below is the other, with a
 *  neutral at parity. */
export function targetIndex(roas: number | null, target: number | null): number | null {
  if (roas === null || target === null || !target) return null;
  return roas / target;
}

export const PRESETS = [
  { value: "7d", label: "Last 7 days" },
  { value: "14d", label: "Last 14 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "60d", label: "Last 60 days" },
  { value: "90d", label: "Last 90 days" },
];
