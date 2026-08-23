import { createContext, useContext, useMemo } from "react";
import { NavLink, Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import { api } from "./lib/api";
import { useAsync } from "./lib/hooks";
import type { Account } from "./lib/types";
import { PRESETS } from "./lib/format";

import FlagCenter from "./screens/FlagCenter";
import Hierarchy from "./screens/Hierarchy";
import AdsetDetailScreen from "./screens/AdsetDetail";
import Leaderboard from "./screens/Leaderboard";
import Tagging from "./screens/Tagging";
import Coverage from "./screens/Coverage";
import CreativeDetail from "./screens/CreativeDetail";
import Settings from "./screens/Settings";

interface Shell {
  accounts: Account[];
  accountId: string | null;
  account: Account | null;
  currency: string;
  preset: string;
  setAccount: (id: string | null) => void;
  setPreset: (preset: string) => void;
  reloadAccounts: () => void;
}

const ShellContext = createContext<Shell | null>(null);

export function useShell(): Shell {
  const shell = useContext(ShellContext);
  if (!shell) throw new Error("useShell outside the app shell");
  return shell;
}

export default function App() {
  const [params, setParams] = useSearchParams();
  const accounts = useAsync(() => api.accounts(), []);

  const list = accounts.data ?? [];
  // The selected client and window live in the URL, so any view can be pasted
  // to someone else and open exactly as it looked.
  const accountId = params.get("client") ?? list[0]?.id ?? null;
  const preset = params.get("preset") ?? "30d";

  const shell = useMemo<Shell>(() => {
    const account = list.find((a) => a.id === accountId) ?? null;
    return {
      accounts: list,
      accountId,
      account,
      currency: account?.currency ?? "INR",
      preset,
      setAccount: (id) => {
        const next = new URLSearchParams(params);
        if (id) next.set("client", id);
        else next.delete("client");
        setParams(next, { replace: false });
      },
      setPreset: (value) => {
        const next = new URLSearchParams(params);
        next.set("preset", value);
        setParams(next, { replace: false });
      },
      reloadAccounts: accounts.reload,
    };
  }, [list, accountId, preset, params, setParams, accounts.reload]);

  const suffix = `?${new URLSearchParams({
    ...(accountId ? { client: accountId } : {}),
    preset,
  })}`;

  return (
    <ShellContext.Provider value={shell}>
      <div className="app">
        <aside className="sidebar">
          <h1>Creative Command Center</h1>
          <p className="tagline">Kill · scale · hold · brief</p>

          <label className="sr-only" htmlFor="client-picker">Client</label>
          <select
            id="client-picker"
            value={accountId ?? ""}
            onChange={(event) => shell.setAccount(event.target.value || null)}
            style={{ width: "100%", marginBottom: 8 }}
          >
            {list.length === 0 ? <option value="">No clients yet</option> : null}
            {list.map((account) => (
              <option key={account.id} value={account.id}>{account.client_name}</option>
            ))}
          </select>

          <label className="sr-only" htmlFor="preset-picker">Date range</label>
          <select
            id="preset-picker"
            value={preset}
            onChange={(event) => shell.setPreset(event.target.value)}
            style={{ width: "100%", marginBottom: 14 }}
          >
            {PRESETS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>

          <nav className="nav">
            <NavLink to={`/flags${suffix}`}>Flag Center</NavLink>
            <NavLink to={`/hierarchy${suffix}`}>Hierarchy Explorer</NavLink>
            <NavLink to={`/leaderboard${suffix}`}>Leaderboard</NavLink>
            <NavLink to={`/coverage${suffix}`}>Coverage</NavLink>
            <NavLink to={`/tagging${suffix}`}>Tagging</NavLink>
            <NavLink to={`/settings${suffix}`}>Settings &amp; sync</NavLink>
          </nav>
        </aside>

        <main className="main">
          {accounts.loading && !accounts.data ? <div className="empty">Loading clients…</div> : null}
          {accounts.error ? (
            <div className="banner crit">
              <span className="icon">⚠</span>
              <span className="grow">
                Could not reach the API. Is the backend running on :8000?
              </span>
              <button onClick={accounts.reload}>Retry</button>
            </div>
          ) : null}

          <Routes>
            <Route path="/" element={<Navigate to={`/flags${suffix}`} replace />} />
            <Route path="/flags" element={<FlagCenter />} />
            <Route path="/hierarchy" element={<Hierarchy />} />
            <Route path="/hierarchy/adset/:adsetId" element={<AdsetDetailScreen />} />
            <Route path="/leaderboard" element={<Leaderboard />} />
            <Route path="/coverage" element={<Coverage />} />
            <Route path="/tagging" element={<Tagging />} />
            <Route path="/creative/:accountId/:creativeId" element={<CreativeDetail />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<div className="empty">No such screen.</div>} />
          </Routes>
        </main>
      </div>
    </ShellContext.Provider>
  );
}
