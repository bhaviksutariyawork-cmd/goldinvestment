import { useState } from "react";
import { useShell } from "../App";
import { api } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import type { Account, Target } from "../lib/types";
import { Empty, ErrorBox, Panel } from "../components/primitives";

/** Sync Settings, targets, and the audit trail.
 *
 *  The access token is write-only from here: it is encrypted at rest and the
 *  API never returns it. */
export default function Settings() {
  const shell = useShell();
  const [message, setMessage] = useState<string | null>(null);

  return (
    <>
      <div className="screen-head">
        <div>
          <h2>Settings &amp; sync</h2>
          <p className="sub">
            One client per ad account. Scheduled sync runs every 4 hours; the first connect
            backfills 90 days, then each run refreshes a rolling 7 days because attribution keeps
            revising recent days.
          </p>
        </div>
      </div>

      {message ? <div className="banner"><span className="grow">{message}</span></div> : null}

      <AccountForm onSaved={(text) => { setMessage(text); shell.reloadAccounts(); }} />

      {shell.accountId ? (
        <>
          <SyncPanel accountId={shell.accountId} />
          <TargetsPanel accountId={shell.accountId} />
          <ActionsPanel accountId={shell.accountId} />
        </>
      ) : (
        <Empty>Add a client to get started.</Empty>
      )}
    </>
  );
}

function AccountForm({ onSaved }: { onSaved: (message: string) => void }) {
  const shell = useShell();
  const existing = shell.account;
  const [draft, setDraft] = useState<Partial<Account> & { access_token?: string }>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const value = (key: keyof Account) =>
    (draft[key] as string | undefined) ?? (existing?.[key] as string | undefined) ?? "";

  const save = async (mode: "update" | "create") => {
    setBusy(true);
    setError(null);
    try {
      const body = {
        client_name: draft.client_name ?? (mode === "update" ? existing?.client_name : "") ?? "",
        meta_ad_account_id:
          draft.meta_ad_account_id ?? (mode === "update" ? existing?.meta_ad_account_id : "") ?? "",
        currency: draft.currency ?? existing?.currency ?? "INR",
        timezone: draft.timezone ?? existing?.timezone ?? "Asia/Kolkata",
        access_token: draft.access_token || undefined,
      };
      if (mode === "update" && existing) await api.updateAccount(existing.id, body);
      else await api.createAccount(body);
      setDraft({});
      onSaved(mode === "update" ? "Client updated." : "Client added.");
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title={existing ? `Client: ${existing.client_name}` : "Add a client"}
      note="The access token is encrypted at rest and never returned by the API."
    >
      <div className="toolbar">
        <label>
          Client name
          <input
            value={value("client_name")}
            onChange={(event) => setDraft({ ...draft, client_name: event.target.value })}
          />
        </label>
        <label>
          Ad account id
          <input
            placeholder="act_1029384756"
            value={value("meta_ad_account_id")}
            onChange={(event) => setDraft({ ...draft, meta_ad_account_id: event.target.value })}
          />
        </label>
        <label>
          Currency
          <input
            style={{ width: 70 }}
            value={value("currency")}
            onChange={(event) => setDraft({ ...draft, currency: event.target.value })}
          />
        </label>
        <label>
          Timezone
          <input
            value={value("timezone")}
            onChange={(event) => setDraft({ ...draft, timezone: event.target.value })}
          />
        </label>
        <label>
          Access token
          <input
            type="password"
            placeholder={existing?.token_hint ?? "not set"}
            value={draft.access_token ?? ""}
            onChange={(event) => setDraft({ ...draft, access_token: event.target.value })}
          />
        </label>
        {existing ? (
          <button disabled={busy} onClick={() => save("update")}>Save</button>
        ) : null}
        <button className="primary" disabled={busy} onClick={() => save("create")}>
          Add as new client
        </button>
      </div>
      {error ? <ErrorBox error={error} /> : null}
    </Panel>
  );
}

function SyncPanel({ accountId }: { accountId: string }) {
  const shell = useShell();
  const status = useAsync(() => api.syncStatus(accountId), [accountId]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [reconciliation, setReconciliation] = useState<Record<string, unknown> | null>(null);

  const run = async (mode: "backfill" | "refresh") => {
    setBusy(true);
    try {
      await api.runSync(accountId, mode);
      setNote(`${mode === "backfill" ? "90-day backfill" : "7-day refresh"} started. Reload in a moment.`);
    } catch (error) {
      setNote(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const reconcile = async () => {
    const range = status.data?.date_range;
    if (!range?.from || !range.to) return;
    setReconciliation(await api.reconcile(accountId, range.from, range.to));
  };

  if (status.error) return <ErrorBox error={status.error} />;
  const data = status.data;

  return (
    <Panel
      title="Sync"
      note={
        data
          ? `${data.snapshot_rows.toLocaleString()} current snapshot rows, ${data.date_range.from ?? "—"} → ${data.date_range.to ?? "—"}`
          : "Loading…"
      }
      right={
        <div className="toolbar">
          <button disabled={busy} onClick={() => run("refresh")}>Refresh 7 days</button>
          <button disabled={busy} onClick={() => run("backfill")}>Backfill 90 days</button>
          <button className="ghost" onClick={() => { status.reload(); setNote(null); }}>Reload</button>
        </div>
      }
    >
      <dl className="kv">
        <dt>Last sync</dt>
        <dd>
          {ago(data?.last_sync_at)}{" "}
          <span className={data?.last_sync_status === "error" ? "delta down" : "muted"}>
            ({data?.last_sync_status ?? "never"})
          </span>
        </dd>
        <dt>API calls last sync</dt><dd>{data?.api_calls_last_sync ?? "—"}</dd>
        {data?.last_sync_error ? (
          <>
            <dt>Error</dt><dd className="delta down">{data.last_sync_error}</dd>
          </>
        ) : null}
      </dl>

      {note ? <div className="note" style={{ marginTop: 8 }}>{note}</div> : null}

      <div style={{ marginTop: 12 }}>
        <div className="toolbar">
          <button className="ghost" onClick={reconcile} disabled={!data?.date_range.from}>
            Reconcile ad-level against campaign-level totals
          </button>
          {reconciliation ? (
            <span className={reconciliation.verdict === "match" ? "delta up" : "delta down"}>
              {String(reconciliation.verdict)} · spend gap{" "}
              {reconciliation.spend_gap === null ? "—" : `${(Number(reconciliation.spend_gap) * 100).toFixed(2)}%`}
            </span>
          ) : null}
        </div>
        <p className="note">
          The two figures come from different Insights calls, so agreement is real evidence the
          grain and the ad-set filters are right. A gap over 1% usually means a batch was missed or
          a <code>level</code> was wrong.
        </p>
      </div>

      <div className="table-wrap scroll-y" style={{ marginTop: 12 }}>
        <table className="grid">
          <thead>
            <tr>
              <th className="left">Started</th>
              <th className="left">Mode</th>
              <th className="left">Status</th>
              <th>Seconds</th>
              <th>API calls</th>
              <th>Async reports</th>
              <th className="left">Steps</th>
            </tr>
          </thead>
          <tbody>
            {(data?.log ?? []).map((entry) => (
              <tr key={entry._id}>
                <td className="left">{new Date(entry.started_at).toLocaleString()}</td>
                <td className="left muted">{entry.mode}</td>
                <td className={`left ${entry.status === "error" ? "delta down" : "delta up"}`}>
                  {entry.status}
                </td>
                <td>{entry.duration_seconds}</td>
                <td>{entry.api_calls}</td>
                <td>{entry.async_reports}</td>
                <td className="left">
                  {entry.steps.map((step) => (
                    <div key={step.step} className="cell-sub">
                      <strong>{step.step}</strong> {step.detail}
                    </div>
                  ))}
                </td>
              </tr>
            ))}
            {!data?.log.length ? (
              <tr><td colSpan={7}><Empty>No syncs recorded yet.</Empty></td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <p className="note">Currency shown across the app: {shell.currency}.</p>
    </Panel>
  );
}

function TargetsPanel({ accountId }: { accountId: string }) {
  const shell = useShell();
  const view = useAsync(() => api.targets(accountId), [accountId]);
  const [draft, setDraft] = useState<Record<string, Partial<Target>>>({});
  const [busy, setBusy] = useState(false);

  const save = async (band: "low" | "high") => {
    const existing = view.data?.targets.find((t) => t.aov_band === band);
    const merged = { ...existing, ...draft[band] } as Target;
    setBusy(true);
    try {
      await api.saveTarget(accountId, band, {
        aov_band: band,
        target_roas: Number(merged.target_roas ?? 0),
        target_cpa: Number(merged.target_cpa ?? 0),
        aov_min: merged.aov_min ?? null,
        aov_max: merged.aov_max ?? null,
      });
      view.reload();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Targets"
      note="One per AOV band. A single client-level ROAS target mislabels both: the same number that is ambitious on a 2,800 AOV product is trivial on a 565 one."
    >
      {view.data?.warning ? (
        <div className="banner warn"><span className="grow">{view.data.warning}</span></div>
      ) : null}
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th className="left">Band</th>
              <th>Target ROAS</th>
              <th>Target CPA</th>
              <th>AOV min</th>
              <th>AOV max</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(["low", "high"] as const).map((band) => {
              const existing = view.data?.targets.find((t) => t.aov_band === band);
              const field = (key: keyof Target) =>
                (draft[band]?.[key] as number | undefined) ?? (existing?.[key] as number | undefined) ?? "";
              const set = (key: keyof Target, value: string) =>
                setDraft({ ...draft, [band]: { ...draft[band], [key]: value === "" ? null : Number(value) } });
              return (
                <tr key={band}>
                  <td className="left">{band}</td>
                  <td><input style={{ width: 70 }} value={field("target_roas")} onChange={(e) => set("target_roas", e.target.value)} /></td>
                  <td><input style={{ width: 80 }} value={field("target_cpa")} onChange={(e) => set("target_cpa", e.target.value)} /></td>
                  <td><input style={{ width: 80 }} value={field("aov_min")} onChange={(e) => set("aov_min", e.target.value)} /></td>
                  <td><input style={{ width: 80 }} value={field("aov_max")} onChange={(e) => set("aov_max", e.target.value)} /></td>
                  <td><button disabled={busy} onClick={() => save(band)}>Save</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="note">
        Amounts in {shell.currency}. A creative in a band with no target lands in HOLD with an
        explicit "set a target" reason rather than a verdict the data cannot support.
      </p>
    </Panel>
  );
}

function ActionsPanel({ accountId }: { accountId: string }) {
  const view = useAsync(() => api.actions(accountId), [accountId]);

  return (
    <Panel
      title="Action log"
      note="Every proposal and every confirmation, including the flags you chose to snooze and why. Nothing here was applied automatically — the app never touches Meta."
      right={<button className="ghost" onClick={view.reload}>Reload</button>}
    >
      <div className="table-wrap scroll-y">
        <table className="grid">
          <thead>
            <tr>
              <th className="left">Proposed</th>
              <th className="left">Action</th>
              <th className="left">Entity</th>
              <th className="left">Because</th>
              <th className="left">Confirmed</th>
            </tr>
          </thead>
          <tbody>
            {(view.data ?? []).map((entry) => (
              <tr key={entry._id}>
                <td className="left">{new Date(entry.proposed_at).toLocaleString()}</td>
                <td className="left">{entry.action.replace(/_/g, " ")}</td>
                <td className="left">
                  {entry.entity_name ?? entry.entity_id}
                  <div className="cell-sub">{entry.entity_type}</div>
                </td>
                <td className="left muted">{entry.reason_flag ?? "—"}</td>
                <td className="left">
                  {entry.confirmed_at
                    ? `${new Date(entry.confirmed_at).toLocaleString()} · ${entry.confirmed_by}`
                    : <span className="chip warn">pending</span>}
                </td>
              </tr>
            ))}
            {!view.data?.length ? (
              <tr><td colSpan={5}><Empty>Nothing proposed yet.</Empty></td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <p className="note">
        Pausing only. Deleting an ad is deliberately not a proposable action — it destroys the
        social proof and the history attached to it.
      </p>
    </Panel>
  );
}
