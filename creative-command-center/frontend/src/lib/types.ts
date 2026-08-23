/** Shapes returned by the API. Mirrors the Python side; nothing is recomputed
 *  in the browser that the engine already decided. */

export type Severity = "red" | "amber" | "blue" | "grey";

export type Status =
  | "PAUSED" | "EXCLUDED" | "INSUFFICIENT" | "LEAKING" | "FATIGUED"
  | "STARVED" | "CUT" | "HOLD" | "WIN";

export interface Metrics {
  spend: number;
  impressions: number;
  reach: number;
  outbound_clicks: number;
  landing_page_views: number;
  add_to_carts: number;
  purchases: number;
  revenue: number;
  roas: number | null;
  cpa: number | null;
  cpm: number | null;
  outbound_ctr: number | null;
  cost_per_outbound_click: number | null;
  lpv_transfer: number | null;
  atc_rate: number | null;
  aov: number | null;
  frequency: number | null;
  frequency_is_lower_bound: boolean;
  reach_basis: string;
  days_live: number;
}

export interface Placement {
  adset_id: string;
  adset_name: string;
  campaign_id: string;
  campaign_name: string;
  spend: number;
  roas: number | null;
  delivery_share: number | null;
  is_best_roas: boolean;
  rival_spend: number;
  rival_roas: number | null;
  ad_ids: string[];
}

export interface CreativeRow {
  creative_id: string;
  account_id: string;
  client_name: string;
  name: string;
  thumbnail_url: string | null;
  ad_ids: string[];
  ad_count: number;
  adset_count: number;
  effective_status: string;
  objective: string;
  category: string | null;
  aov_band: string | null;
  angle_id: string | null;
  format: string | null;
  hook_type: string | null;
  offer_type: string | null;
  lp_type: string | null;
  target_roas: number | null;
  target_cpa: number | null;
  metrics: Metrics;
  trailing7: Metrics;
  lifetime: Metrics;
  status: Status;
  reason: string;
  action: string;
  upper_funnel_verdict: string | null;
  streak: number;
  rank: number | null;
  prior_rank: number | null;
  rank_movement: number | null;
  is_ranked: boolean;
  badge?: "gold" | "silver" | "bronze" | null;
  placements: Placement[];
}

export interface TestingRow {
  creative_id: string;
  name: string;
  client_name: string;
  account_id: string;
  thumbnail_url: string | null;
  category: string | null;
  aov_band: string | null;
  angle_id: string | null;
  status: Status;
  reason: string;
  upper_funnel_verdict: string | null;
  ad_ids: string[];
  metrics: Partial<Metrics> & { spend: number };
}

export interface WindowSpec { start: string; end: string; days: number }

export interface BundleMeta {
  account_id?: string;
  client_name?: string;
  currency?: string;
  as_of: string | null;
  settled_through?: string;
  settling_window?: WindowSpec;
  window: WindowSpec | null;
  benchmarks?: Record<string, number | null>;
  targets?: Target[];
  accounts?: BundleMeta[];
}

export interface Flag {
  key: string;
  label: string;
  severity: Severity;
  why: string;
  entity_type: string;
  entity_id: string;
  entity_name: string;
  account_id: string;
  client_name: string;
  value: number | null;
  threshold: number | null;
  trigger: string;
  detail: string;
  money_at_stake: number;
  money_label: string;
  proposal: Record<string, unknown> | null;
  dedupe_key: string;
  context: Record<string, unknown>;
}

export interface FlagGroup {
  severity: Severity;
  label: string;
  count: number;
  money_at_stake: number;
  flags: Flag[];
}

export interface UntaggedFigure {
  untagged_spend: number;
  tagged_spend: number;
  total_spend: number;
  untagged_share: number;
  threshold: number;
  visible: boolean;
}

export interface ClientSummary {
  account_id: string;
  client_name: string;
  currency: string;
  as_of: string;
  spend: number;
  revenue: number;
  purchases: number;
  roas: number;
  flags: Record<Severity, number>;
  red_money: number;
  statuses: Record<string, number>;
  untagged: UntaggedFigure;
  targets_complete: boolean;
  last_sync_at: string | null;
  last_sync_status: string | null;
  settling_window: WindowSpec;
}

export interface Account {
  id: string;
  client_name: string;
  meta_ad_account_id: string;
  currency: string;
  timezone: string;
  has_token: boolean;
  token_hint: string | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
  api_calls_last_sync: number | null;
}

export interface Target {
  id?: string;
  account_id?: string;
  aov_band: "low" | "high";
  target_roas: number;
  target_cpa: number;
  aov_min: number | null;
  aov_max: number | null;
}

export interface HierarchyRow {
  id: string;
  name: string;
  status: string;
  objective: string;
  level: "campaign" | "adset" | "ad";
  campaign_id: string;
  campaign_name: string;
  adset_id: string;
  adset_name: string;
  creative_id: string;
  thumbnail_url: string | null;
  delivery_share: number | null;
  delivery_share_of: string;
  metrics: Metrics;
  category: string | null;
  aov_band: string | null;
  angle_id: string | null;
  target_roas: number | null;
  target_cpa: number | null;
  roas_vs_target: number | null;
  flags: { key: string; label: string; severity: Severity; trigger: string }[];
}

export interface DeliverySegment {
  ad_id: string;
  ad_name: string;
  creative_id: string;
  thumbnail_url: string | null;
  spend: number;
  delivery_share: number;
  roas: number | null;
  purchases: number;
}

export interface AdsetDetail {
  meta: BundleMeta;
  adset_id: string;
  adset_name: string;
  delivery_bar: DeliverySegment[];
  ads: HierarchyRow[];
  learning_threshold: {
    events_7d: number; threshold: number; share: number;
    under_threshold: boolean; window: WindowSpec;
  };
  budget_pacing: {
    days: { date: string; spend: number; daily_budget: number | null; pacing: number | null }[];
    spend_7d: number; budget_7d: number; pacing: number | null; window: WindowSpec;
  };
  misallocation: {
    present: boolean;
    best_ad: DeliverySegment;
    widest_ad: DeliverySegment;
    gap_pct: number | null;
  };
}

export interface WithinAdsetGroup {
  adset_id: string;
  adset_name: string;
  campaign_id: string;
  campaign_name: string;
  client_name: string;
  account_id: string;
  spend: number;
  misallocated: boolean;
  misallocation_gap_pct: number | null;
  creatives: {
    creative_id: string;
    name: string;
    thumbnail_url: string | null;
    ad_ids: string[];
    delivery_share: number | null;
    metrics: Metrics;
    category: string | null;
    aov_band: string | null;
    target_roas: number | null;
    status: Status;
    rank_in_adset: number;
  }[];
}

export interface TaggingRow {
  creative_id: string;
  name: string;
  thumbnail_url: string | null;
  ad_ids: string[];
  ad_count: number;
  spend: number;
  purchases: number;
  roas: number | null;
  days_live: number;
  campaigns: string[];
  tags: Record<string, string | null>;
  complete: boolean;
  notes: string | null;
}

export interface CoverageCell {
  category: string;
  angle_id: string;
  impressions: number;
  spend: number;
  roas: number | null;
  creatives: number;
  tested: boolean;
  state: "tested" | "partial" | "untested";
}

export interface CoveragePayload {
  meta: BundleMeta;
  matrix: {
    categories: string[];
    angles: string[];
    cells: CoverageCell[];
    impression_floor: number;
    untested_cells: CoverageCell[];
  };
  concentration: {
    aov_band: string; hhi: number; threshold: number; concentrated: boolean;
    angles: number; spend: number; top_angle: string; top_share: number;
    shares: { angle_id: string; share: number; spend: number }[];
  }[];
  priority_queue: {
    category: string; spend: number; spend_share: number; angles_total: number;
    angles_untested: number; untested_share: number; roas_recent: number | null;
    roas_prior: number | null; trend: number | null; trend_factor: number;
    score: number; untested_angles: string[];
  }[];
  untagged: UntaggedFigure;
  formula: string;
}

export interface SeriesPoint extends Metrics {
  date: string;
  settling: boolean;
}

export interface ActionLogEntry {
  _id: string;
  account_id: string;
  entity_type: string;
  entity_id: string;
  entity_name: string | null;
  action: string;
  reason_flag: string | null;
  proposed_at: string;
  confirmed_at: string | null;
  confirmed_by: string | null;
  prior_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  note?: string | null;
}

export interface SyncStatusPayload {
  account_id: string;
  client_name: string;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
  api_calls_last_sync: number | null;
  snapshot_rows: number;
  date_range: { from: string | null; to: string | null };
  log: {
    _id: string; mode: string; started_at: string; finished_at: string;
    duration_seconds: number; status: string; error: string | null;
    api_calls: number; async_reports: number;
    rows_written: Record<string, number>;
    steps: { step: string; detail: string }[];
  }[];
}
