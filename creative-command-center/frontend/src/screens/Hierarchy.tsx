import { Link, useSearchParams } from "react-router-dom";
import { useShell } from "../App";
import { api } from "../lib/api";
import { money, pct, ratio } from "../lib/format";
import { useAsync, useSort } from "../lib/hooks";
import type { HierarchyRow } from "../lib/types";
import { Empty, ErrorBox, METRIC_HEADERS, MetricCells, SettlingNote } from "../components/primitives";
import { roasColor } from "../components/charts";

/** Screen A. Campaign → Ad Set → Ads, with the same columns at every level.
 *
 *  Position lives entirely in the URL — level, campaign, ad set, window — so
 *  browser back works and any view can be pasted to someone else. */
export default function Hierarchy() {
  const shell = useShell();
  const [params, setParams] = useSearchParams();

  const level = (params.get("level") ?? "campaign") as "campaign" | "adset" | "ad";
  const campaignId = params.get("campaign") ?? undefined;
  const adsetId = params.get("adset") ?? undefined;
  const allAds = params.get("all_ads") === "1";

  const effectiveLevel = allAds ? "ad" : level;

  const view = useAsync(
    () =>
      shell.accountId
        ? api.hierarchy(shell.accountId, {
            level: effectiveLevel,
            campaign_id: campaignId,
            adset_id: adsetId,
            preset: shell.preset,
          })
        : Promise.resolve(null),
    [shell.accountId, effectiveLevel, campaignId, adsetId, shell.preset],
  );

  const sorter = useSort<HierarchyRow>("delivery_share");

  const navigate = (next: Record<string, string | undefined>) => {
    const merged = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === undefined) merged.delete(key);
      else merged.set(key, value);
    }
    setParams(merged);
  };

  if (!shell.accountId) return <Empty>Add a client on the Settings screen first.</Empty>;
  if (view.error) return <ErrorBox error={view.error} />;

  const rows = view.data?.rows ?? [];
  const sorted = sorter.sort(rows, (row, key) => {
    if (key === "name") return row.name;
    if (key === "delivery_share") return row.delivery_share;
    return (row.metrics as unknown as Record<string, number | null>)[key];
  });

  const drill = (row: HierarchyRow) => {
    if (effectiveLevel === "campaign") navigate({ level: "adset", campaign: row.id, adset: undefined });
    else if (effectiveLevel === "adset") navigate({ level: "ad", adset: row.id });
  };

  return (
    <>
      <div className="screen-head">
        <div>
          <h2>Hierarchy Explorer</h2>
          <p className="sub">
            Delivery share is the column that matters. At ad level it is the ad's share of its
            parent ad set's spend — the only place an ad set quietly funding a worse creative
            becomes visible.
          </p>
        </div>
        <div className="toolbar">
          <label>
            <input
              type="checkbox"
              checked={allAds}
              onChange={(event) => navigate({ all_ads: event.target.checked ? "1" : undefined })}
            />
            All Ads
          </label>
          <SettlingNote
            window={view.data?.meta.window}
            settling={view.data?.meta.settling_window}
          />
        </div>
      </div>

      <nav className="breadcrumb">
        <button className="ghost" onClick={() => navigate({ level: "campaign", campaign: undefined, adset: undefined })}>
          All campaigns
        </button>
        {(view.data?.breadcrumb ?? []).slice(1).map((crumb) => (
          <span key={crumb.label}>
            <span className="sep">/</span>{" "}
            <button
              className="ghost"
              onClick={() => navigate({ level: crumb.level, campaign: crumb.campaign_id, adset: crumb.adset_id })}
            >
              {crumb.label}
            </button>
          </span>
        ))}
        {allAds ? <span className="chip warn">flattened</span> : null}
      </nav>

      <div className="panel">
        <header>
          <div>
            <h3>
              {effectiveLevel === "campaign" ? "Campaigns" : effectiveLevel === "adset" ? "Ad sets" : "Ads"}
              <span className="muted"> · {rows.length}</span>
            </h3>
            <div className="note">
              Delivery share is measured against the {rows[0]?.delivery_share_of ?? "parent"}.
            </div>
          </div>
        </header>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="left sortable" onClick={() => sorter.toggle("name")}>Name</th>
                <th className="left">Status</th>
                <th className="sortable" onClick={() => sorter.toggle("delivery_share")}>
                  Delivery {sorter.key === "delivery_share" ? (sorter.desc ? "▾" : "▴") : ""}
                </th>
                {METRIC_HEADERS.map((header) => (
                  <th key={header.key} className="sortable" onClick={() => sorter.toggle(header.key)}>
                    {header.label} {sorter.key === header.key ? (sorter.desc ? "▾" : "▴") : ""}
                  </th>
                ))}
                <th className="left">Flags</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => (
                <tr key={row.id}>
                  <td className="left name">
                    <span className="cell-stack">
                      {/* A thumbnail above ad level would be whichever creative
                          happened to be last in the group — meaningless. */}
                      {effectiveLevel === "ad" && row.thumbnail_url ? (
                        <img className="thumb" src={row.thumbnail_url} alt="" loading="lazy" />
                      ) : null}
                      <span>
                        {effectiveLevel === "ad" ? (
                          <Link to={`/creative/${shell.accountId}/${row.creative_id}?preset=${shell.preset}`}>
                            {row.name}
                          </Link>
                        ) : (
                          <button className="ghost" onClick={() => drill(row)}>{row.name}</button>
                        )}
                        {effectiveLevel === "ad" && allAds ? (
                          <div className="cell-sub">{row.adset_name}</div>
                        ) : null}
                        {effectiveLevel === "adset" ? (
                          <div className="cell-sub">
                            <Link to={`/hierarchy/adset/${row.id}?client=${shell.accountId}&preset=${shell.preset}`}>
                              open detail
                            </Link>
                          </div>
                        ) : null}
                      </span>
                    </span>
                  </td>
                  <td className="left muted">{row.status}</td>
                  <td>
                    <span
                      title={`${pct(row.delivery_share, 1)} of its ${row.delivery_share_of}, ROAS ${ratio(row.metrics.roas)}`}
                      style={{
                        display: "inline-block",
                        minWidth: 46,
                        padding: "1px 5px",
                        borderRadius: 3,
                        // Only coloured where there is a target to colour against:
                        // campaigns and ad sets span AOV bands, so a single ROAS
                        // verdict on them would be an opinion the data cannot support.
                        background: row.target_roas
                          ? roasColor(row.metrics.roas, row.target_roas)
                          : "var(--surface-3)",
                        border: "1px solid var(--border)",
                      }}
                    >
                      {pct(row.delivery_share)}
                    </span>
                  </td>
                  <MetricCells metrics={row.metrics} currency={shell.currency} target={row.target_roas} />
                  <td className="left">
                    {row.flags.slice(0, 3).map((flag) => (
                      <span key={flag.key} className="chip" title={flag.trigger}>
                        {flag.label}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
              {!sorted.length && !view.loading ? (
                <tr><td colSpan={12}><Empty>No delivery in this window.</Empty></td></tr>
              ) : null}
            </tbody>
            {sorted.length ? (
              <tfoot>
                <tr>
                  <td className="left muted">Total</td>
                  <td /><td />
                  <td className="muted">
                    {money(sorted.reduce((sum, row) => sum + row.metrics.spend, 0), shell.currency)}
                  </td>
                  <td className="muted">
                    {ratio(
                      sorted.reduce((sum, row) => sum + row.metrics.revenue, 0) /
                        Math.max(sorted.reduce((sum, row) => sum + row.metrics.spend, 0), 1e-9),
                    )}
                  </td>
                  <td />
                  <td className="muted">
                    {Math.round(sorted.reduce((sum, row) => sum + row.metrics.purchases, 0))}
                  </td>
                  <td colSpan={5} />
                </tr>
              </tfoot>
            ) : null}
          </table>
        </div>
      </div>
    </>
  );
}
