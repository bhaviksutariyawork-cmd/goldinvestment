import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useShell } from "../App";
import { api } from "../lib/api";
import { money, pct, ratio } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { DeliveryBar, RoasLegend } from "../components/charts";
import { Empty, ErrorBox, Panel, RankMovement, StatusPill } from "../components/primitives";

const BADGE = { gold: "🥇", silver: "🥈", bronze: "🥉" } as const;

/** Screen B. Ranked globally, and then — on the second tab — ranked against
 *  the only comparison that ever actually happened. */
export default function Leaderboard() {
  const shell = useShell();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "within" ? "within" : "global";
  const scopeAll = params.get("scope") === "all";

  const setParam = (key: string, value: string | undefined) => {
    const next = new URLSearchParams(params);
    if (value === undefined) next.delete(key);
    else next.set(key, value);
    setParams(next);
  };

  const filters = {
    category: params.get("category") ?? undefined,
    aov_band: params.get("aov_band") ?? undefined,
    status: params.get("status") ?? undefined,
    angle_id: params.get("angle") ?? undefined,
    format: params.get("format") ?? undefined,
  };
  const accountId = scopeAll ? undefined : shell.accountId ?? undefined;

  return (
    <>
      <div className="screen-head">
        <div>
          <h2>Leaderboard</h2>
          <p className="sub">
            Aggregated by creative, so an asset running in three ad sets is one row here and three
            rows in the Hierarchy Explorer. Anything under 30 purchases has no rank — it sits in
            Testing below, judged on the hook.
          </p>
        </div>
        <div className="toolbar">
          <button className={tab === "global" ? "primary" : ""} onClick={() => setParam("tab", "global")}>
            Global
          </button>
          <button className={tab === "within" ? "primary" : ""} onClick={() => setParam("tab", "within")}>
            Within Ad Set
          </button>
          <label>
            <input
              type="checkbox"
              checked={scopeAll}
              onChange={(event) => setParam("scope", event.target.checked ? "all" : undefined)}
            />
            All clients
          </label>
        </div>
      </div>

      {tab === "global" ? (
        <GlobalTab accountId={accountId} filters={filters} onFilter={setParam} />
      ) : (
        <WithinTab accountId={accountId} />
      )}
    </>
  );
}

function GlobalTab({
  accountId, filters, onFilter,
}: {
  accountId: string | undefined;
  filters: Record<string, string | undefined>;
  onFilter: (key: string, value: string | undefined) => void;
}) {
  const shell = useShell();
  const view = useAsync(
    () => api.leaderboard({ account_id: accountId, preset: shell.preset, ...filters }),
    [accountId, shell.preset, JSON.stringify(filters)],
  );

  if (view.error) return <ErrorBox error={view.error} />;
  const options = view.data?.filters ?? {};

  return (
    <>
      <div className="toolbar" style={{ marginBottom: 10 }}>
        {([
          ["category", "categories", "Category"],
          ["aov_band", "aov_bands", "AOV band"],
          ["angle", "angles", "Angle"],
          ["format", "formats", "Format"],
          ["status", "statuses", "Status"],
        ] as const).map(([param, optionKey, label]) => (
          <label key={param}>
            {label}
            <select
              value={filters[param === "angle" ? "angle_id" : param] ?? ""}
              onChange={(event) => onFilter(param, event.target.value || undefined)}
            >
              <option value="">All</option>
              {(options[optionKey] ?? []).map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
        ))}
      </div>

      <Panel
        title="Ranked"
        note={`${view.data?.counts.ranked ?? 0} creatives past the 30-purchase gate. Rank movement compares two equal-length windows ending at D-3 and D-10, both clear of the settling lag.`}
      >
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>#</th>
                <th className="left">Creative</th>
                <th className="left">Client</th>
                <th className="left">Category</th>
                <th className="left">Band</th>
                <th>Spend</th>
                <th>ROAS</th>
                <th>CPA</th>
                <th>Purch</th>
                <th>Days</th>
                <th className="left">Status</th>
                <th>Streak</th>
                <th>Move</th>
              </tr>
            </thead>
            <tbody>
              {(view.data?.ranked ?? []).map((row) => (
                <tr key={`${row.account_id}:${row.creative_id}`}>
                  <td>
                    {row.badge ? <span className="badge">{BADGE[row.badge]}</span> : row.rank}
                  </td>
                  <td className="left name">
                    <span className="cell-stack">
                      {row.thumbnail_url ? <img className="thumb" src={row.thumbnail_url} alt="" loading="lazy" /> : null}
                      <span>
                        <Link to={`/creative/${row.account_id}/${row.creative_id}?preset=${shell.preset}`}>
                          {row.name}
                        </Link>
                        <div className="cell-sub">
                          {row.adset_count} ad {row.adset_count === 1 ? "set" : "sets"} ·{" "}
                          {row.ad_count} {row.ad_count === 1 ? "ad" : "ads"}
                        </div>
                      </span>
                    </span>
                  </td>
                  <td className="left muted">{row.client_name}</td>
                  <td className="left">{row.category ?? <span className="chip warn">untagged</span>}</td>
                  <td className="left muted">{row.aov_band ?? "—"}</td>
                  <td>{money(row.metrics.spend, shell.currency)}</td>
                  <td title={row.target_roas ? `${ratio(row.metrics.roas! / row.target_roas)}x target` : undefined}>
                    {ratio(row.metrics.roas)}
                  </td>
                  <td>{money(row.metrics.cpa, shell.currency)}</td>
                  <td>{Math.round(row.metrics.purchases)}</td>
                  <td>{row.metrics.days_live}</td>
                  <td className="left"><StatusPill status={row.status} title={row.reason} /></td>
                  <td title="Consecutive settled days holding WIN">{row.streak || "—"}</td>
                  <td><RankMovement movement={row.rank_movement} /></td>
                </tr>
              ))}
              {!view.data?.ranked.length && !view.loading ? (
                <tr><td colSpan={13}><Empty>Nothing clears the gate in this window.</Empty></td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel
        title="Testing"
        note="Under 30 purchases, so no rank and no ROAS verdict. Sorted by cost per outbound click — cheap clicks are the signal at this sample size. CTR is a diagnostic here, never a kill trigger."
      >
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="left">Creative</th>
                <th className="left">Client</th>
                <th className="left">Category</th>
                <th>Spend</th>
                <th>Cost / click</th>
                <th>Out CTR</th>
                <th>LPV %</th>
                <th>CPM</th>
                <th>Impr</th>
                <th>Days</th>
                <th className="left">Hook read</th>
              </tr>
            </thead>
            <tbody>
              {(view.data?.testing ?? []).map((row) => (
                <tr key={`${row.account_id}:${row.creative_id}`}>
                  <td className="left name">
                    <span className="cell-stack">
                      {row.thumbnail_url ? <img className="thumb" src={row.thumbnail_url} alt="" loading="lazy" /> : null}
                      <Link to={`/creative/${row.account_id}/${row.creative_id}?preset=${shell.preset}`}>
                        {row.name}
                      </Link>
                    </span>
                  </td>
                  <td className="left muted">{row.client_name}</td>
                  <td className="left">{row.category ?? <span className="chip warn">untagged</span>}</td>
                  <td>{money(row.metrics.spend, shell.currency)}</td>
                  <td>{money(row.metrics.cost_per_outbound_click ?? null, shell.currency)}</td>
                  <td>{pct(row.metrics.outbound_ctr ?? null, 2)}</td>
                  <td>{pct(row.metrics.lpv_transfer ?? null)}</td>
                  <td>{money(row.metrics.cpm ?? null, shell.currency)}</td>
                  <td>{Math.round(row.metrics.impressions ?? 0).toLocaleString("en-IN")}</td>
                  <td>{row.metrics.days_live ?? "—"}</td>
                  <td className="left" title={row.reason}>
                    <span className="chip">{(row.upper_funnel_verdict ?? "").replace(/_/g, " ").toLowerCase() || "—"}</span>
                  </td>
                </tr>
              ))}
              {!view.data?.testing.length && !view.loading ? (
                <tr><td colSpan={11}><Empty>No creatives under the gate.</Empty></td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}

function WithinTab({ accountId }: { accountId: string | undefined }) {
  const shell = useShell();
  const [openOnly, setOpenOnly] = useState(true);
  const view = useAsync(
    () => api.withinAdset({ account_id: accountId, preset: shell.preset }),
    [accountId, shell.preset],
  );

  if (view.error) return <ErrorBox error={view.error} />;
  const groups = (view.data?.groups ?? []).filter((g) => (openOnly ? g.misallocated : true));

  return (
    <>
      <div className="banner">
        <span className="icon">●</span>
        <span className="grow">
          Ads in different ad sets never competed for the same budget, so a global rank comparing
          them is portfolio theatre. {view.data?.misallocated_count ?? 0} ad{" "}
          {view.data?.misallocated_count === 1 ? "set is" : "sets are"} funding a worse-ROAS
          creative over a better one.
        </span>
        <label>
          <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} />
          Misallocated only
        </label>
      </div>

      {groups.map((group) => {
        const target = group.creatives[0]?.target_roas ?? null;
        return (
          <Panel
            key={`${group.account_id}:${group.adset_id}`}
            title={group.adset_name}
            note={`${group.campaign_name} · ${group.client_name} · ${money(group.spend, shell.currency)}`}
            right={
              group.misallocated ? (
                <span className="chip warn">
                  {group.misallocation_gap_pct}% delivery gap the wrong way
                </span>
              ) : null
            }
          >
            <DeliveryBar
              currency={shell.currency}
              segments={group.creatives.map((creative) => ({
                id: creative.creative_id,
                label: creative.name,
                share: creative.delivery_share ?? 0,
                roas: creative.metrics.roas,
                spend: creative.metrics.spend,
                purchases: Math.round(creative.metrics.purchases),
                target: creative.target_roas,
              }))}
            />
            <div className="table-wrap" style={{ marginTop: 10 }}>
              <table className="grid">
                <thead>
                  <tr>
                    <th>#</th>
                    <th className="left">Creative</th>
                    <th>Delivery</th>
                    <th>Spend</th>
                    <th>ROAS</th>
                    <th>CPA</th>
                    <th>Purch</th>
                    <th>Freq</th>
                    <th className="left">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {group.creatives.map((creative) => (
                    <tr key={creative.creative_id}>
                      <td>{creative.rank_in_adset}</td>
                      <td className="left name">
                        <span className="cell-stack">
                          {creative.thumbnail_url ? (
                            <img className="thumb" src={creative.thumbnail_url} alt="" loading="lazy" />
                          ) : null}
                          <Link to={`/creative/${group.account_id}/${creative.creative_id}?preset=${shell.preset}`}>
                            {creative.name}
                          </Link>
                        </span>
                      </td>
                      <td>{pct(creative.delivery_share)}</td>
                      <td>{money(creative.metrics.spend, shell.currency)}</td>
                      <td>{ratio(creative.metrics.roas)}</td>
                      <td>{money(creative.metrics.cpa, shell.currency)}</td>
                      <td>{Math.round(creative.metrics.purchases)}</td>
                      <td>{ratio(creative.metrics.frequency)}</td>
                      <td className="left"><StatusPill status={creative.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ marginTop: 8 }}><RoasLegend target={target} /></div>
          </Panel>
        );
      })}

      {!groups.length && !view.loading ? (
        <Empty>
          {openOnly
            ? "No ad set is funding the wrong creative right now. Uncheck the filter to see them all."
            : "No ad sets delivered in this window."}
        </Empty>
      ) : null}
    </>
  );
}
