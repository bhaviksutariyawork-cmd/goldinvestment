import { useState } from "react";
import { Link } from "react-router-dom";
import { useShell } from "../App";
import { api } from "../lib/api";
import { money, moneyExact, pct } from "../lib/format";
import { useAsync } from "../lib/hooks";
import type { Flag, Severity } from "../lib/types";
import { Empty, ErrorBox, Tile } from "../components/primitives";

const SEVERITY_ORDER: Severity[] = ["red", "amber", "blue", "grey"];
const SEVERITY_ICON: Record<Severity, string> = { red: "■", amber: "▲", blue: "●", grey: "◇" };
const SEVERITY_MEANING: Record<Severity, string> = {
  red: "losing money now",
  amber: "degrading",
  blue: "opportunity",
  grey: "data quality",
};

export default function FlagCenter() {
  const shell = useShell();
  const [scope, setScope] = useState<"all" | "client">("all");
  const [collapsed, setCollapsed] = useState<Set<Severity>>(new Set(["grey"]));
  const accountFilter = scope === "client" ? shell.accountId ?? undefined : undefined;

  const flags = useAsync(
    () => api.flags({ account_id: accountFilter, preset: shell.preset }),
    [accountFilter, shell.preset],
  );
  const dashboard = useAsync(() => api.dashboard(shell.preset), [shell.preset]);

  const toggle = (severity: Severity) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(severity)) next.delete(severity);
      else next.add(severity);
      return next;
    });

  if (flags.error) return <ErrorBox error={flags.error} />;

  const groups = flags.data?.groups ?? [];
  const totals = dashboard.data?.flag_totals ?? {};

  return (
    <>
      <div className="screen-head">
        <div>
          <h2>Flag Center</h2>
          <p className="sub">
            Every problem across every client, grouped by severity and ranked inside each group by
            money at stake. Red is losing money now; amber is degrading; blue is an opportunity;
            grey is a data-quality problem that makes the rest less trustworthy.
          </p>
        </div>
        <div className="toolbar">
          <label>
            <input
              type="checkbox"
              checked={scope === "client"}
              onChange={(event) => setScope(event.target.checked ? "client" : "all")}
            />
            This client only
          </label>
          <button onClick={() => { flags.reload(); dashboard.reload(); }}>Refresh</button>
        </div>
      </div>

      <div className="tiles">
        {SEVERITY_ORDER.map((severity) => (
          <Tile
            key={severity}
            tone={severity}
            label={`${SEVERITY_ICON[severity]} ${SEVERITY_MEANING[severity]}`}
            value={totals[severity] ?? 0}
            foot={
              severity === "red"
                ? `${money(
                    groups.find((g) => g.severity === "red")?.money_at_stake ?? 0,
                    shell.currency,
                  )} at stake`
                : undefined
            }
          />
        ))}
      </div>

      <TaggingBanner clients={flags.data?.clients ?? []} />

      {flags.loading && !flags.data ? <Empty>Reading every client…</Empty> : null}

      {groups.map((group) => {
        const isCollapsed = collapsed.has(group.severity);
        return (
          <section className={`sev-group sev-${group.severity}`} key={group.severity}>
            <header onClick={() => toggle(group.severity)}>
              <span className="marker" />
              <h3>{group.label}</h3>
              <span className="count">
                {group.count} {group.count === 1 ? "flag" : "flags"}
              </span>
              <span className="money">
                {group.severity === "blue" ? "recoverable " : ""}
                {money(group.money_at_stake, shell.currency)}
              </span>
              <span className="muted">{isCollapsed ? "▸" : "▾"}</span>
            </header>
            {!isCollapsed ? (
              group.flags.length ? (
                <div className="flag-list">
                  {group.flags.map((flag) => (
                    <FlagCard key={flag.dedupe_key} flag={flag} onChange={flags.reload} />
                  ))}
                </div>
              ) : (
                <Empty>Nothing in this group. That is the goal.</Empty>
              )
            ) : null}
          </section>
        );
      })}
    </>
  );
}

function TaggingBanner({
  clients,
}: { clients: { account_id: string; client_name: string; untagged: { untagged_share: number; visible: boolean; untagged_spend: number } }[] }) {
  const shell = useShell();
  const needy = clients.filter((c) => c.untagged.visible);
  if (!needy.length) return null;
  return (
    <div className="banner warn">
      <span className="icon">◇</span>
      <span className="grow">
        Untagged spend is still visible on{" "}
        {needy.map((client, index) => (
          <span key={client.account_id}>
            {index > 0 ? ", " : ""}
            <strong>{client.client_name}</strong> ({pct(client.untagged.untagged_share)},{" "}
            {money(client.untagged.untagged_spend, shell.currency)})
          </span>
        ))}
        . Coverage, concentration and the testing queue all read that table.
      </span>
      <Link to={`/tagging?client=${needy[0].account_id}&preset=${shell.preset}`}>
        <button className="primary">Tag creatives</button>
      </Link>
    </div>
  );
}

function FlagCard({ flag, onChange }: { flag: Flag; onChange: () => void }) {
  const shell = useShell();
  const [busy, setBusy] = useState(false);
  const [proposed, setProposed] = useState<string | null>(null);
  const [snoozing, setSnoozing] = useState(false);
  const [reason, setReason] = useState("");
  const [days, setDays] = useState<7 | 14 | 30>(7);
  const [message, setMessage] = useState<string | null>(null);

  const proposal = (flag.proposal ?? {}) as Record<string, unknown>;
  const action = (proposal.action as string) ?? null;

  const propose = async () => {
    if (!action) return;
    setBusy(true);
    try {
      const entry = await api.propose(flag.account_id, {
        entity_type: flag.entity_type === "creative" ? "creative" : flag.entity_type,
        entity_id: flag.entity_id,
        entity_name: flag.entity_name,
        action,
        reason_flag: flag.key,
        new_value: { trigger: flag.trigger },
        ad_ids: (proposal.ad_ids as string[]) ?? [],
      });
      setProposed(entry._id);
      setMessage("Proposed. Nothing has changed in Meta — confirm to record the decision.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!proposed) return;
    setBusy(true);
    try {
      await api.confirm(flag.account_id, proposed, { confirmed_by: "operator" });
      setMessage("Confirmed and logged. Apply it in Ads Manager — pause, never delete.");
      onChange();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const snooze = async () => {
    setBusy(true);
    try {
      await api.snooze({
        dedupe_key: flag.dedupe_key,
        account_id: flag.account_id,
        entity_type: flag.entity_type,
        entity_id: flag.entity_id,
        flag_key: flag.key,
        days,
        reason,
      });
      onChange();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
      setSnoozing(false);
    }
  };

  return (
    <article className={`flag-card ${flag.severity}`}>
      <div className="top">
        <span className="label">
          {SEVERITY_ICON[flag.severity]} {flag.label}
        </span>
        <span className="money" title={flag.money_label}>
          {moneyExact(flag.money_at_stake, shell.currency)}
        </span>
      </div>

      <div className="entity">
        {flag.entity_type === "creative" ? (
          <Link to={`/creative/${flag.account_id}/${flag.entity_id}?preset=${shell.preset}`}>
            {flag.entity_name}
          </Link>
        ) : (
          flag.entity_name
        )}
        <span className="muted"> · {flag.client_name}</span>
      </div>

      <div className="trigger">{flag.trigger}</div>
      <div className="why">{flag.detail}</div>
      <div className="why"><em>{flag.why}</em></div>
      <div className="money muted">{flag.money_label}</div>

      {message ? <div className="why">{message}</div> : null}

      {snoozing ? (
        <div className="actions">
          <select value={days} onChange={(e) => setDays(Number(e.target.value) as 7 | 14 | 30)}>
            <option value={7}>7 days</option>
            <option value={14}>14 days</option>
            <option value={30}>30 days</option>
          </select>
          <input
            placeholder="Reason (required)"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            style={{ flex: 1, minWidth: 140 }}
          />
          <button disabled={reason.trim().length < 3 || busy} onClick={snooze}>Snooze</button>
          <button className="ghost" onClick={() => setSnoozing(false)}>Cancel</button>
        </div>
      ) : (
        <div className="actions">
          {action && !proposed ? (
            <button disabled={busy} onClick={propose}>Propose {action.replace(/_/g, " ")}</button>
          ) : null}
          {proposed ? (
            <>
              <button className="primary" disabled={busy} onClick={confirm}>Confirm</button>
              <button
                className="ghost"
                disabled={busy}
                onClick={async () => {
                  await api.withdraw(flag.account_id, proposed);
                  setProposed(null);
                  setMessage(null);
                }}
              >
                Withdraw
              </button>
            </>
          ) : null}
          <button className="ghost" onClick={() => setSnoozing(true)}>Snooze</button>
        </div>
      )}
    </article>
  );
}
