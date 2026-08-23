import { useCallback, useEffect, useRef, useState } from "react";

export interface Async<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

/** Fetch on mount and whenever `deps` change, discarding responses that arrive
 *  after a newer request has already been issued. */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]): Async<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const generation = useRef(0);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(loader, deps);

  useEffect(() => {
    const mine = ++generation.current;
    setLoading(true);
    run()
      .then((result) => {
        if (mine === generation.current) { setData(result); setError(null); }
      })
      .catch((cause) => {
        if (mine === generation.current) { setError(cause); setData(null); }
      })
      .finally(() => {
        if (mine === generation.current) setLoading(false);
      });
  }, [run, nonce]);

  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}

/** Sort state for a table, kept in component memory rather than the URL —
 *  a sort is a glance, not a position worth sharing. */
export function useSort<T>(initialKey: string, initialDesc = true) {
  const [key, setKey] = useState(initialKey);
  const [desc, setDesc] = useState(initialDesc);

  const toggle = (next: string) => {
    if (next === key) setDesc((d) => !d);
    else { setKey(next); setDesc(true); }
  };

  const sort = (rows: T[], value: (row: T, key: string) => number | string | null) =>
    [...rows].sort((a, b) => {
      const left = value(a, key);
      const right = value(b, key);
      if (left === null) return 1;
      if (right === null) return -1;
      if (typeof left === "string" || typeof right === "string") {
        return desc
          ? String(right).localeCompare(String(left))
          : String(left).localeCompare(String(right));
      }
      return desc ? right - left : left - right;
    });

  return { key, desc, toggle, sort };
}
