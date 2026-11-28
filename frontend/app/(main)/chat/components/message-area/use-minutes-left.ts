import { useEffect, useState } from 'react';

/** Whole minutes left (rounded up), re-read every half minute; 0 once the request has expired. */
export function useMinutesLeft(expiresAt: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const left = expiresAt - now;
    // NaN: no time on it, nothing to read again.
    if (!(left > 0)) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.min(left, 30_000));
    return () => clearTimeout(timer);
  }, [now, expiresAt]);
  return Math.max(0, Math.ceil((expiresAt - now) / 60_000));
}
