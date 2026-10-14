"use client";

import { useEffect, useState } from "react";

/** Runs `fetcher` now and every `intervalMs`, keeping the latest result.
 *  `fetcher` must be stable (wrap in useCallback). `reload()` refetches now.
 *  On failure the last good data is kept, `failed` is set and `error` holds the cause. */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs = 10000) {
  const [data, setData] = useState<T | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const run = () =>
      fetcher()
        .then((d) => {
          if (cancelled) return;
          setData(d);
          setFailed(false);
          setError(null);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          setFailed(true);
          setError(e);
        });
    run();
    const timer = setInterval(run, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [fetcher, intervalMs, nonce]);

  return { data, failed, error, reload: () => setNonce((n) => n + 1) };
}
