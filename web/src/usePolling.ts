import { useCallback, useEffect, useRef, useState } from "react";

export interface Polled<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  refresh: () => void;
}

/** Jednoduchý polling: načte hned, pak každých `intervalMs`; pauza, když je appka na pozadí,
 *  a okamžité načtení při návratu (visibilitychange / focus / pageshow / online). */
export function usePolling<T>(fn: (() => Promise<T>) | null, intervalMs: number, deps: unknown[] = []): Polled<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let inFlight = false;
    const run = async () => {
      const f = fnRef.current;
      if (!f || inFlight) return;
      inFlight = true;
      if (document.visibilityState === "visible") {
        setLoading(true);
        try {
          const d = await f();
          if (!cancelled) {
            setData(d);
            setError(undefined);
          }
        } catch (e) {
          if (!cancelled) setError(e as Error);
        } finally {
          if (!cancelled) setLoading(false);
        }
      }
      inFlight = false;
      if (!cancelled) timer = window.setTimeout(run, intervalMs);
    };
    run();
    // návrat do appky (přepnutí z jiné appky, odemčení telefonu, obnovení sítě) -> hned načíst
    let lastKick = 0;
    const onVis = () => {
      if (document.visibilityState === "visible" && !inFlight && Date.now() - lastKick > 1_000) {
        lastKick = Date.now();
        window.clearTimeout(timer);
        run();
      }
    };
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("focus", onVis);
    window.addEventListener("pageshow", onVis);
    window.addEventListener("online", onVis);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("focus", onVis);
      window.removeEventListener("pageshow", onVis);
      window.removeEventListener("online", onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, fn === null, ...deps]);

  return { data, error, loading, refresh };
}
