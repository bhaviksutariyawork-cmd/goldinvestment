import { Link, useParams } from "react-router-dom";
import { useShell } from "../App";
import { api } from "../lib/api";
import { money, pct, ratio } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { DeliveryBar, PacingChart, RoasLegend } from "../components/charts";
import { Empty, ErrorBox, METRIC_HEADERS, MetricCells, Panel } from "../components/primitives";

/** Ad-set detail: the delivery bar, the 50-event learning line, and pacing. */
export default function AdsetDetail() {
  const shell = useShell();
  const { adsetId = "" } = useParams();

  const view = useAsync(
    () =>
      shell.accountId
        ? api.adsetDetail(shell.accountId, adsetId, shell.preset)
        : Promise.resolve(null),
    [shell.accountId, adsetId, shell.preset],
  );

  if (view.error) return <ErrorBox error={view.error} />;
  if (!view.data) return <Empty>{view.loading ? "Loading…" : "Nothing here."}</Empty>;

  const detail = view.data;
  const target = detail.ads[0]?.target_roas ?? null;
  const learning = detail.learning_threshold;
  const pacing = detail.budget_pacing;

  return (
    <>
      <div className="screen-head">
        <div>
          <nav className="breadcrumb">
            <Link to={`/hierarchy?client=${shell.accountId}&preset=${shell.preset}`}>All campaigns</Link>
            <span className="sep">/</span>
            <span>{detail.ads[0]?.campaign_name}</span>
          </nav>
          <h2>{detail.adset_name}</h2>
          <p className="sub">
            Everything below compares only ads that competed for this ad set's budget. Nothing here
            is ranked against an ad in a different ad set — they never bid against each other.
          </p>
        </div>
      </div>

      {detail.misallocation.present ? (
        <div className="banner crit">
          <span className="icon">■</span>
          <span className="grow">
            <strong>{detail.misallocation.widest_ad.ad_name}</strong> holds{" "}
            {pct(detail.misallocation.widest_ad.delivery_share)} of the budget at ROAS{" "}
            {ratio(detail.misallocation.widest_ad.roas)}, while{" "}
            <strong>{detail.misallocation.best_ad.ad_name}</strong> earns{" "}
            {ratio(detail.misallocation.best_ad.roas)} on{" "}
            {pct(detail.misallocation.best_ad.delivery_share)}
            {detail.misallocation.gap_pct !== null
              ? ` — a ${detail.misallocation.gap_pct}% delivery gap the wrong way.`
              : "."}
          </span>
        </div>
      ) : null}

      <Panel
        title="Delivery share"
        note="Each ad's share of this ad set's spend, coloured by ROAS against its target."
      >
        <DeliveryBar
          currency={shell.currency}
          segments={detail.delivery_bar.map((segment) => ({
            id: segment.ad_id,
            label: segment.ad_name,
            share: segment.delivery_share,
            roas: segment.roas,
            spend: segment.spend,
            purchases: segment.purchases,
            target,
          }))}
        />
        <div style={{ marginTop: 8 }}>
          <RoasLegend target={target} />
        </div>
      </Panel>

      <div className="split">
        <Panel
          title="Learning threshold"
          note={`Optimisation events in the trailing settled 7 days (${learning.window.start} → ${learning.window.end}).`}
        >
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <span style={{ fontSize: 28 }}>{learning.events_7d}</span>
            <span className="muted">of {learning.threshold}</span>
          </div>
          <div
            style={{
              height: 8, borderRadius: 4, background: "var(--surface-3)",
              marginTop: 8, overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${Math.min(learning.share, 1) * 100}%`,
                height: "100%",
                background: learning.under_threshold ? "var(--warning)" : "var(--good)",
              }}
            />
          </div>
          <p className="note" style={{ marginTop: 8 }}>
            {learning.under_threshold
              ? "Below 50 events a week Meta's delivery is guesswork, and so is any read of this ad set's numbers. Consolidate ad sets or widen the audience before drawing conclusions."
              : "Past the learning threshold — the delivery here is optimised, not exploratory."}
          </p>
        </Panel>

        <Panel
          title="Budget pacing"
          note={`Actual daily spend against the budget set that day. ${pct(pacing.pacing)} of plan over 7 days.`}
        >
          {pacing.days.length ? (
            <PacingChart days={pacing.days} currency={shell.currency} />
          ) : (
            <Empty>No budget history synced for this ad set yet.</Empty>
          )}
        </Panel>
      </div>

      <Panel title="Ads" note={`${detail.ads.length} ads competing for ${money(pacing.spend_7d, shell.currency)} a week.`}>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="left">Ad</th>
                <th className="left">Status</th>
                <th>Delivery</th>
                {METRIC_HEADERS.map((header) => <th key={header.key}>{header.label}</th>)}
                <th className="left">Flags</th>
              </tr>
            </thead>
            <tbody>
              {detail.ads.map((ad) => (
                <tr key={ad.id}>
                  <td className="left name">
                    <span className="cell-stack">
                      {ad.thumbnail_url ? <img className="thumb" src={ad.thumbnail_url} alt="" /> : null}
                      <Link to={`/creative/${shell.accountId}/${ad.creative_id}?preset=${shell.preset}`}>
                        {ad.name}
                      </Link>
                    </span>
                  </td>
                  <td className="left muted">{ad.status}</td>
                  <td>{pct(ad.delivery_share)}</td>
                  <MetricCells metrics={ad.metrics} currency={shell.currency} target={ad.target_roas} />
                  <td className="left">
                    {ad.flags.map((flag) => (
                      <span key={flag.key} className="chip" title={flag.trigger}>{flag.label}</span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
