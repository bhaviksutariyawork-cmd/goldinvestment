import { useShell } from "../App";
import { api } from "../lib/api";
import { count, money, pct, ratio } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { coverageColor, coverageInk, SERIES, TipRows, useTooltip } from "../components/charts";
import { Empty, ErrorBox, Panel } from "../components/primitives";

/** The Coverage module — what to brief next.
 *
 *  Everything here reads `creative_meta`, so the untagged figure travels with
 *  it: a grid built on 60% of spend has holes that are not really holes. */
export default function Coverage() {
  const shell = useShell();
  const tip = useTooltip();
  const view = useAsync(
    () => (shell.accountId ? api.coverage(shell.accountId, "90d") : Promise.resolve(null)),
    [shell.accountId],
  );

  if (!shell.accountId) return <Empty>Add a client on the Settings screen first.</Empty>;
  if (view.error) return <ErrorBox error={view.error} />;
  if (!view.data) return <Empty>{view.loading ? "Loading…" : "Nothing to show."}</Empty>;

  const { matrix, concentration, priority_queue: queue, untagged, formula } = view.data;
  const cellIndex = new Map(matrix.cells.map((cell) => [`${cell.category}|${cell.angle_id}`, cell]));
  const maxImpressions = Math.max(...matrix.cells.map((cell) => cell.impressions), 1);

  return (
    <>
      <div className="screen-head">
        <div>
          <h2>Coverage</h2>
          <p className="sub">
            Which category needs new creative. Cells under{" "}
            {count(matrix.impression_floor)} cumulative impressions are drawn as untested — an angle
            you have not really tried is not the same as one that failed, and treating them alike is
            how live angles get written off.
          </p>
        </div>
      </div>

      {untagged.visible ? (
        <div className="banner warn">
          <span className="icon">◇</span>
          <span className="grow">
            {pct(untagged.untagged_share, 1)} of spend is untagged, so this grid is incomplete.
            Some cells below are empty only because nothing has been tagged into them.
          </span>
        </div>
      ) : null}

      <Panel
        title="Coverage matrix"
        note="Cumulative impressions per category × angle, across the full history."
      >
        <div className="table-wrap">
          <table className="matrix">
            <thead>
              <tr>
                <th className="row-head" />
                {matrix.angles.map((angle) => <th key={angle}>{angle}</th>)}
              </tr>
            </thead>
            <tbody>
              {matrix.categories.map((category) => (
                <tr key={category}>
                  <th className="row-head">{category}</th>
                  {matrix.angles.map((angle) => {
                    const cell = cellIndex.get(`${category}|${angle}`);
                    const impressions = cell?.impressions ?? 0;
                    const state = cell?.state ?? "untested";
                    return (
                      <td
                        key={angle}
                        className={`cell ${state}`}
                        style={
                          state === "tested"
                            ? {
                                background: coverageColor(impressions, maxImpressions),
                                color: coverageInk(impressions, maxImpressions),
                              }
                            : undefined
                        }
                        onMouseMove={(event) =>
                          tip.show(event, (
                            <TipRows
                              title={`${category} × ${angle}`}
                              rows={[
                                ["Impressions", count(impressions)],
                                ["Spend", money(cell?.spend ?? 0, shell.currency)],
                                ["ROAS", ratio(cell?.roas ?? null)],
                                ["Creatives", String(cell?.creatives ?? 0)],
                                ["State", state],
                              ]}
                            />
                          ))
                        }
                        onMouseLeave={tip.hide}
                      >
                        {state === "untested" ? "untested" : count(impressions)}
                        {cell?.roas !== null && cell?.roas !== undefined && state === "tested" ? (
                          <div style={{ fontSize: 10, opacity: 0.85 }}>{ratio(cell.roas)}x</div>
                        ) : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="legend" style={{ marginTop: 10 }}>
          <span className="muted">Cumulative impressions:</span>
          {[0.05, 0.3, 0.6, 0.85, 1].map((fraction) => (
            <span className="key" key={fraction}>
              <span
                className="swatch"
                style={{ background: coverageColor(maxImpressions * fraction, maxImpressions) }}
              />
              {count(maxImpressions * fraction)}
            </span>
          ))}
          <span className="key">
            <span
              className="swatch"
              style={{
                background:
                  "repeating-linear-gradient(135deg, transparent, transparent 3px, rgba(255,255,255,0.25) 3px, rgba(255,255,255,0.25) 4px)",
                border: "1px dashed var(--border-strong)",
              }}
            />
            untested
          </span>
        </div>
        {tip.node}
      </Panel>

      <Panel title="Testing priority queue" note={formula}>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="left">Category</th>
                <th>Score</th>
                <th>Spend</th>
                <th>Spend share</th>
                <th>Angles untested</th>
                <th>ROAS now</th>
                <th>ROAS prior</th>
                <th>Trend factor</th>
                <th className="left">Brief these angles</th>
              </tr>
            </thead>
            <tbody>
              {queue.map((entry) => (
                <tr key={entry.category}>
                  <td className="left">{entry.category}</td>
                  <td>{entry.score.toFixed(4)}</td>
                  <td>{money(entry.spend, shell.currency)}</td>
                  <td>{pct(entry.spend_share)}</td>
                  <td>{entry.angles_untested} / {entry.angles_total}</td>
                  <td>{ratio(entry.roas_recent)}</td>
                  <td>{ratio(entry.roas_prior)}</td>
                  <td
                    className={entry.trend_factor > 1 ? "delta down" : entry.trend_factor < 1 ? "delta up" : "muted"}
                    title="Above 1 means ROAS is falling, which raises the urgency of new creative."
                  >
                    {entry.trend_factor.toFixed(2)}
                  </td>
                  <td className="left">
                    {entry.untested_angles.map((angle) => (
                      <span className="chip" key={angle}>{angle}</span>
                    ))}
                  </td>
                </tr>
              ))}
              {!queue.length ? (
                <tr><td colSpan={9}><Empty>Nothing tagged yet — the queue reads creative_meta.</Empty></td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel
        title="Concentration"
        note="HHI on spend share by angle, within each AOV band. Above 0.25 the band is one fatigue event away from a hole."
      >
        {concentration.map((band) => (
          <div key={band.aov_band} style={{ marginBottom: 14 }}>
            <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginBottom: 6 }}>
              <strong>{band.aov_band} band</strong>
              <span className={band.concentrated ? "delta down" : "muted"}>
                HHI {band.hhi.toFixed(3)} vs {band.threshold}
              </span>
              <span className="muted">
                {band.angles} angles · {money(band.spend, shell.currency)}
              </span>
              {band.concentrated ? (
                <span className="chip warn">
                  {pct(band.top_share)} on {band.top_angle}
                </span>
              ) : null}
            </div>
            <div style={{ display: "flex", gap: 2, height: 18 }}>
              {/* One colour for every segment: width already carries the share,
                  and colouring by rank would repaint the survivors whenever an
                  angle drops out. */}
              {band.shares.map((share) => (
                <div
                  key={share.angle_id}
                  title={`${share.angle_id} · ${pct(share.share, 1)} · ${money(share.spend, shell.currency)}`}
                  style={{
                    flexGrow: share.share,
                    flexBasis: 0,
                    borderRadius: 3,
                    border: "1px solid var(--border)",
                    background: SERIES.spend,
                  }}
                />
              ))}
            </div>
            <div className="legend" style={{ marginTop: 6 }}>
              {band.shares.map((share) => (
                <span className="key" key={share.angle_id}>
                  {share.angle_id} {pct(share.share)}
                </span>
              ))}
            </div>
          </div>
        ))}
        {!concentration.length ? <Empty>No tagged spend to measure.</Empty> : null}
      </Panel>
    </>
  );
}
