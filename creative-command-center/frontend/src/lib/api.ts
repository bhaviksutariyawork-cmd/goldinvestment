import type {
  Account, ActionLogEntry, AdsetDetail, BundleMeta, ClientSummary, CoveragePayload,
  CreativeRow, Flag, FlagGroup, HierarchyRow, SeriesPoint, SyncStatusPayload, Target,
  TaggingRow, TestingRow, UntaggedFigure, WithinAdsetGroup,
} from "./types";

const BASE = "/api";

async function get<T>(path: string, params: Record<string, string | undefined | null> = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, value);
  }
  const suffix = query.toString() ? `?${query}` : "";
  const response = await fetch(`${BASE}${path}${suffix}`);
  if (!response.ok) throw new Error(await errorText(response));
  return response.json() as Promise<T>;
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await errorText(response));
  return response.status === 204 ? (undefined as T) : (response.json() as Promise<T>);
}

async function errorText(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    return payload.detail ?? `${response.status} ${response.statusText}`;
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

export interface DashboardPayload {
  clients: ClientSummary[];
  flag_totals: Record<string, number>;
  settling_days: number;
  window: { start: string; end: string; days: number } | null;
}

export const api = {
  dashboard: (preset: string) => get<DashboardPayload>("/dashboard", { preset }),

  accounts: () => get<Account[]>("/accounts"),
  createAccount: (body: unknown) => send<Account>("POST", "/accounts", body),
  updateAccount: (id: string, body: unknown) => send<Account>("PUT", `/accounts/${id}`, body),
  targets: (id: string) =>
    get<{ targets: Target[]; bands_set: string[]; complete: boolean; warning: string | null }>(
      `/accounts/${id}/targets`,
    ),
  saveTarget: (id: string, band: string, body: Target) =>
    send<Target>("PUT", `/accounts/${id}/targets/${band}`, body),

  syncStatus: (id: string) => get<SyncStatusPayload>(`/sync/${id}`),
  runSync: (id: string, mode: "backfill" | "refresh") =>
    send<{ status: string }>("POST", `/sync/${id}`, { mode }),
  reconcile: (id: string, since: string, until: string) =>
    get<Record<string, unknown>>(`/sync/${id}/reconcile`, { since, until }),

  hierarchy: (id: string, params: Record<string, string | undefined>) =>
    get<{
      meta: BundleMeta; level: string; rows: HierarchyRow[];
      breadcrumb: { label: string; level: string; campaign_id?: string; adset_id?: string }[];
    }>(`/hierarchy/${id}`, params),
  adsetDetail: (id: string, adsetId: string, preset: string) =>
    get<AdsetDetail>(`/hierarchy/${id}/adset/${adsetId}`, { preset }),

  leaderboard: (params: Record<string, string | undefined>) =>
    get<{
      meta: BundleMeta; ranked: CreativeRow[]; testing: TestingRow[];
      filters: Record<string, string[]>; counts: { ranked: number; testing: number };
    }>("/leaderboard", params),
  withinAdset: (params: Record<string, string | undefined>) =>
    get<{ meta: BundleMeta; groups: WithinAdsetGroup[]; misallocated_count: number }>(
      "/leaderboard/within-adset", params,
    ),

  flags: (params: Record<string, string | undefined>) =>
    get<{
      groups: FlagGroup[]; total: number;
      clients: { account_id: string; client_name: string; untagged: UntaggedFigure; as_of: string }[];
      catalogue: Record<string, { label: string; severity: string; why: string }>;
    }>("/flags", params),
  snooze: (body: unknown) => send<{ snoozed_until: string }>("POST", "/flags/snooze", body),
  unsnooze: (key: string) => send<unknown>("DELETE", `/flags/snooze/${encodeURIComponent(key)}`),
  snoozes: (accountId?: string) =>
    get<{ dedupe_key: string; flag_key: string; entity_id: string; reason: string; snoozed_until: string }[]>(
      "/flags/snoozes", { account_id: accountId },
    ),

  tagging: (id: string, onlyUntagged: boolean) =>
    get<{
      meta: BundleMeta; rows: TaggingRow[]; untagged: UntaggedFigure;
      vocabulary: Record<string, string[]>; total: number;
    }>(`/tagging/${id}`, { only_untagged: String(onlyUntagged) }),
  bulkTag: (id: string, creativeIds: string[], tags: Record<string, string | null>) =>
    send<{ updated: number }>("POST", `/tagging/${id}/bulk`, { creative_ids: creativeIds, tags }),

  coverage: (id: string, preset: string) => get<CoveragePayload>(`/coverage/${id}`, { preset }),

  creative: (accountId: string, creativeId: string, preset: string) =>
    get<{
      meta: BundleMeta; creative: CreativeRow; series: SeriesPoint[];
      settled_through: string; flags: Flag[];
      by_adset: (Record<string, unknown> & { adset_id: string })[];
      ads: [string, string, string][];
    }>(`/creatives/${accountId}/${creativeId}`, { preset }),

  propose: (accountId: string, body: unknown) =>
    send<ActionLogEntry>("POST", `/actions/${accountId}/propose`, body),
  confirm: (accountId: string, actionId: string, body: unknown) =>
    send<ActionLogEntry>("POST", `/actions/${accountId}/${actionId}/confirm`, body),
  withdraw: (accountId: string, actionId: string) =>
    send<unknown>("DELETE", `/actions/${accountId}/${actionId}`),
  actions: (accountId: string, pendingOnly = false) =>
    get<ActionLogEntry[]>(`/actions/${accountId}`, { pending_only: String(pendingOnly) }),

  rules: () => get<Record<string, unknown>>("/rules"),
};
