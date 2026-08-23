import { useMemo, useState } from "react";
import { useShell } from "../App";
import { api } from "../lib/api";
import { money, pct, ratio } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Empty, ErrorBox, Panel } from "../components/primitives";

const FIELDS = [
  ["category", "Category"],
  ["aov_band", "AOV band"],
  ["angle_id", "Angle"],
  ["format", "Format"],
  ["hook_type", "Hook"],
  ["offer_type", "Offer"],
  ["lp_type", "Landing page"],
] as const;

type Field = (typeof FIELDS)[number][0];

/** The bulk-tagging screen.
 *
 *  `creative_meta` cannot be derived from the API — most spend sits on
 *  numeric-only ad names — and every Coverage answer depends on it. Built for
 *  speed: sorted by spend, multi-select, one tag applied to all at once. */
export default function Tagging() {
  const shell = useShell();
  const [onlyUntagged, setOnlyUntagged] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [draft, setDraft] = useState<Partial<Record<Field, string>>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const view = useAsync(
    () => (shell.accountId ? api.tagging(shell.accountId, onlyUntagged) : Promise.resolve(null)),
    [shell.accountId, onlyUntagged],
  );

  const rows = view.data?.rows ?? [];
  const vocabulary = view.data?.vocabulary ?? {};
  const untagged = view.data?.untagged;

  const selectedSpend = useMemo(
    () => rows.filter((row) => selected.has(row.creative_id)).reduce((sum, row) => sum + row.spend, 0),
    [rows, selected],
  );

  const toggle = (creativeId: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(creativeId)) next.delete(creativeId);
      else next.add(creativeId);
      return next;
    });

  const apply = async () => {
    if (!shell.accountId || !selected.size) return;
    const tags = Object.fromEntries(Object.entries(draft).filter(([, value]) => value));
    if (!Object.keys(tags).length) return;
    setBusy(true);
    try {
      const result = await api.bulkTag(shell.accountId, [...selected], tags);
      setMessage(`Tagged ${result.updated} creatives.`);
      setSelected(new Set());
      setDraft({});
      view.reload();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  if (!shell.accountId) return <Empty>Add a client on the Settings screen first.</Empty>;
  if (view.error) return <ErrorBox error={view.error} />;

  return (
    <>
      <div className="screen-head">
        <div>
          <h2>Tagging</h2>
          <p className="sub">
            Ad names are unreliable — most spend sits on numeric-only names like <code>112-4</code>,
            and nothing in this table can be derived from the API. Work down by spend: that is what
            moves the untagged share, which is the number that decides when this screen can be put
            away.
          </p>
        </div>
        <div className="toolbar">
          <label>
            <input
              type="checkbox"
              checked={onlyUntagged}
              onChange={(event) => { setOnlyUntagged(event.target.checked); setSelected(new Set()); }}
            />
            Untagged only
          </label>
        </div>
      </div>

      {untagged ? (
        <div className={`banner ${untagged.visible ? "warn" : ""}`}>
          <span className="icon">{untagged.visible ? "◇" : "✓"}</span>
          <span className="grow">
            <strong>{pct(untagged.untagged_share, 1)}</strong> of spend is untagged (
            {money(untagged.untagged_spend, shell.currency)} of{" "}
            {money(untagged.total_spend, shell.currency)}).{" "}
            {untagged.visible
              ? "This figure stays on the dashboard until it drops under 10%."
              : "Under the 10% threshold — Coverage can be trusted."}
          </span>
        </div>
      ) : null}

      <Panel
        title={`Apply to ${selected.size} selected`}
        note={
          selected.size
            ? `${money(selectedSpend, shell.currency)} of spend. Only the fields you set are written — sweeping a category across forty creatives will not clear the angles you set yesterday.`
            : "Select rows below, then set one or more fields and apply."
        }
        right={
          <div className="toolbar">
            <button onClick={() => setSelected(new Set(rows.map((r) => r.creative_id)))}>
              Select all {rows.length}
            </button>
            <button className="ghost" onClick={() => setSelected(new Set())}>Clear</button>
            <button
              className="primary"
              disabled={!selected.size || busy || !Object.values(draft).some(Boolean)}
              onClick={apply}
            >
              Apply
            </button>
          </div>
        }
      >
        <div className="toolbar">
          {FIELDS.map(([field, label]) => (
            <label key={field}>
              {label}
              <input
                list={`vocab-${field}`}
                value={draft[field] ?? ""}
                placeholder="—"
                onChange={(event) => setDraft({ ...draft, [field]: event.target.value })}
                style={{ width: 120 }}
              />
              <datalist id={`vocab-${field}`}>
                {(vocabulary[field] ?? []).map((value) => <option key={value} value={value} />)}
              </datalist>
            </label>
          ))}
        </div>
        {message ? <div className="note" style={{ marginTop: 8 }}>{message}</div> : null}
      </Panel>

      <Panel title="Creatives" note={`${rows.length} shown, highest spend first.`}>
        <div className="table-wrap scroll-y">
          <table className="grid">
            <thead>
              <tr>
                <th className="left" style={{ width: 28 }} />
                <th className="left">Creative</th>
                <th>Spend</th>
                <th>ROAS</th>
                <th>Purch</th>
                <th>Days</th>
                {FIELDS.map(([field, label]) => <th key={field} className="left">{label}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.creative_id} className={selected.has(row.creative_id) ? "selected" : ""}>
                  <td className="left">
                    <input
                      type="checkbox"
                      checked={selected.has(row.creative_id)}
                      onChange={() => toggle(row.creative_id)}
                      aria-label={`Select ${row.name}`}
                    />
                  </td>
                  <td className="left name">
                    <span className="cell-stack">
                      {row.thumbnail_url ? (
                        <img className="thumb" src={row.thumbnail_url} alt="" loading="lazy" />
                      ) : null}
                      <span>
                        {row.name}
                        <div className="cell-sub">
                          {row.ad_count} {row.ad_count === 1 ? "ad" : "ads"} ·{" "}
                          {row.campaigns.join(", ") || "no campaign"}
                        </div>
                      </span>
                    </span>
                  </td>
                  <td>{money(row.spend, shell.currency)}</td>
                  <td>{ratio(row.roas)}</td>
                  <td>{row.purchases}</td>
                  <td>{row.days_live}</td>
                  {FIELDS.map(([field]) => (
                    <td key={field} className="left">
                      {row.tags[field] ?? <span className="muted">—</span>}
                    </td>
                  ))}
                </tr>
              ))}
              {!rows.length && !view.loading ? (
                <tr>
                  <td colSpan={12}>
                    <Empty>
                      {onlyUntagged ? "Everything with delivery is tagged." : "No creatives yet."}
                    </Empty>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
