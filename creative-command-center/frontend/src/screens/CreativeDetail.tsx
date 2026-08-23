import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { count, money, pct, ratio } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { SERIES, TrendChart } from "../components/charts";
import { Empty, ErrorBox, Panel, StatusPill } from "../components/primitives";

/** One creative: the verdict, why it holds, and the history behind it.
 *
 *  Every chart bands the trailing settling days rather than plotting them as
 *  fact — the last three points are still moving. */
export default function CreativeDetail() {
  const { accountId = "", creativeId = "" } = useParams();
  const [params] = useSearchParams();
  const preset = params.get("preset") ?? "90d";

  const view = useAsync(
    () => api.creative(accountId, creativeId, preset),
    [accountId, creativeId, preset],
  );

  if (view.error) return <ErrorBox error={view.error} />;
  if (!view.data) return <Empty>{view.loading ? "Loading…" : "No such creative."}</Empty>;

  const { creative, series, flags, meta } = view.data;
  const currency = meta.currency ?? "INR";
  const dates = series.map((point) => point.date);
  const settling = series.map((point) => point.settling);

  return (
    <>
      <div className="screen-head">
        <div>
          <nav className="breadcrumb">
            <Link to={`/leaderboard?client=${accountId}&preset=${preset}`}>Leaderboard</Link>
            <span className="sep">/</span>
            <span>{creative.client_name}</span>
          </nav>
          <h2 style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {creative.thumbnail_url ? (
              <img className="thumb lg" src={creative.thumbnail_url} alt="" />
            ) : null}
            {creative.name}
            <StatusPill status={creative.status} />
          </h2>
          <p className="sub">{creative.reason}</p>
          <p className="sub">
            <strong>Do:</strong> {creative.action}
            {creative.upper_funnel_verdict ? (
              <>
                {" · "}
                <strong>Hook read:</strong>{" "}
                {creative.upper_funnel_verdict.replace(/_/g, " ").toLowerCase()} at{" "}
                {money(creative.metrics.cost_per_outbound_click, currency)} per outbound click
              </>
            ) : null}
          </p>
        </div>
      </div>

      <div className="tiles">
        <TileRow label="Spend" value={money(creative.metrics.spend, currency)} foot={`${creative.metrics.days_live} days live`} />
        <TileRow
          label="ROAS"
          value={ratio(creative.metrics.roas)}
          foot={creative.target_roas ? `target ${ratio(creative.target_roas)} · ${ratio((creative.metrics.roas ?? 0) / creative.target_roas)}x` : "no target for this band"}
        />
        <TileRow label="Purchases" value={Math.round(creative.metrics.purchases)} foot={creative.metrics.purchases >= 30 ? "past the verdict gate" : "under the 30-purchase gate"} />
        <TileRow label="CPA" value={money(creative.metrics.cpa, currency)} foot={creative.target_cpa ? `target ${money(creative.target_cpa, currency)}` : undefined} />
        <TileRow
          label="Frequency"
          value={`${ratio(creative.metrics.frequency)}${creative.metrics.frequency_is_lower_bound ? "+" : ""}`}
          foot={creative.metrics.frequency_is_lower_bound ? `${creative.metrics.reach_basis.replace(/_/g, " ")} — a floor` : "deduplicated reach"}
        />
        <TileRow label="LPV transfer" value={pct(creative.metrics.lpv_transfer)} foot="floor 60%" />
        <TileRow label="Cost / outbound click" value={money(creative.metrics.cost_per_outbound_click, currency)} foot={`p75 ${money((meta.benchmarks?.cost_per_outbound_click_p75 ?? null) as number | null, currency)}`} />
        <TileRow label="Streak" value={creative.streak || "—"} foot="consecutive settled days holding WIN" />
      </div>

      {flags.length ? (
        <Panel title="Flags" note="Everything raised against this creative or the ad sets it runs in.">
          {flags.map((flag) => (
            <div className={`flag-card ${flag.severity}`} key={flag.dedupe_key} style={{ marginBottom: 6 }}>
              <div className="top">
                <span className="label">{flag.label}</span>
                <span className="money" title={flag.money_label}>
                  {money(flag.money_at_stake, currency)}
                </span>
              </div>
              <div className="entity">
                {flag.entity_name}
                <span className="muted"> · {flag.entity_type}</span>
              </div>
              <div className="trigger">{flag.trigger}</div>
              <div className="why">{flag.detail}</div>
            </div>
          ))}
        </Panel>
      ) : null}

      <Panel
        title="Trend"
        note={`Settled through ${view.data.settled_through}. The banded days on the right are attribution-incomplete — Meta is still revising them, and reading a decline into them is the most common way to kill something that is fine.`}
      >
        <div style={{ display: "grid", gap: 14 }}>
          <TrendChart
            dates={dates}
            settling={settling}
            series={{
              key: "spend", label: "Spend", colour: SERIES.spend,
              values: series.map((point) => point.spend),
              formatter: (value) => money(value, currency),
            }}
          />
          <TrendChart
            dates={dates}
            settling={settling}
            series={{
              key: "roas", label: "ROAS", colour: SERIES.roas,
              values: series.map((point) => point.roas),
              formatter: (value) => ratio(value),
            }}
          />
          <TrendChart
            dates={dates}
            settling={settling}
            series={{
              key: "purchases", label: "Purchases", colour: SERIES.purchases,
              values: series.map((point) => point.purchases),
              formatter: (value) => count(value),
            }}
          />
          <TrendChart
            dates={dates}
            settling={settling}
            series={{
              key: "cpoc", label: "Cost per outbound click", colour: SERIES.spend,
              values: series.map((point) => point.cost_per_outbound_click),
              formatter: (value) => money(value, currency),
            }}
          />
        </div>
        <div className="legend" style={{ marginTop: 10 }}>
          <span className="key">
            <span className="swatch" style={{ background: "rgba(255,255,255,0.12)" }} /> settling — not final
          </span>
          <span className="muted">
            Each measure gets its own chart. Two scales on one axis is the fastest way to read a
            relationship that is not there.
          </span>
        </div>
      </Panel>

      <Panel title="Where it runs" note="Aggregated once for the Leaderboard; here is every ad set it competed in.">
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="left">Ad set</th>
                <th className="left">Campaign</th>
                <th>Spend</th>
                <th>Delivery share</th>
                <th>ROAS</th>
                <th>Rest of ad set</th>
                <th className="left">Best in set</th>
              </tr>
            </thead>
            <tbody>
              {creative.placements.map((placement) => (
                <tr key={placement.adset_id}>
                  <td className="left">
                    <Link to={`/hierarchy/adset/${placement.adset_id}?client=${accountId}&preset=${preset}`}>
                      {placement.adset_name}
                    </Link>
                  </td>
                  <td className="left muted">{placement.campaign_name}</td>
                  <td>{money(placement.spend, currency)}</td>
                  <td>{pct(placement.delivery_share)}</td>
                  <td>{ratio(placement.roas)}</td>
                  <td title={`${money(placement.rival_spend, currency)} spent on everything else`}>
                    {ratio(placement.rival_roas)}
                  </td>
                  <td className="left">
                    {placement.is_best_roas ? <span className="chip warn">yes</span> : <span className="muted">no</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Tags" note="Coverage, concentration and the testing queue all read these.">
        <dl className="kv">
          <dt>Category</dt><dd>{creative.category ?? "—"}</dd>
          <dt>AOV band</dt><dd>{creative.aov_band ?? "—"}</dd>
          <dt>Angle</dt><dd>{creative.angle_id ?? "—"}</dd>
          <dt>Format</dt><dd>{creative.format ?? "—"}</dd>
          <dt>Hook</dt><dd>{creative.hook_type ?? "—"}</dd>
          <dt>Offer</dt><dd>{creative.offer_type ?? "—"}</dd>
          <dt>Landing page</dt><dd>{creative.lp_type ?? "—"}</dd>
          <dt>Ad ids</dt><dd className="muted">{creative.ad_ids.join(", ")}</dd>
        </dl>
      </Panel>
    </>
  );
}

function TileRow({ label, value, foot }: { label: string; value: React.ReactNode; foot?: React.ReactNode }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {foot ? <div className="foot">{foot}</div> : null}
    </div>
  );
}
