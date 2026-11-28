'use client';

import { useEffect, useState } from 'react';

/** The clock a list's times are read against: it ticks every `everyMs` while `ticking`, and not at all otherwise. */
export function useNow(ticking: boolean, everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(timer);
  }, [ticking, everyMs]);
  return now;
}
