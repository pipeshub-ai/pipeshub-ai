'use client';

import { useEffect, useState } from 'react';
import { addLocalDays, startOfLocalDay } from '@/lib/utils/local-day';

/**
 * The start of the viewer's local today, which moves on at local midnight. Day labels computed from it re-render
 * when the day turns, so a list left open overnight does not keep calling yesterday's rows "Today".
 */
export function useLocalDay(): number {
  const [day, setDay] = useState(() => startOfLocalDay(Date.now()));

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const sync = () => {
      const today = startOfLocalDay(Date.now());
      setDay((prev) => (prev === today ? prev : today));
    };
    const schedule = () => {
      clearTimeout(timer);
      const now = Date.now();
      timer = setTimeout(() => {
        sync();
        schedule();
      }, Math.max(0, addLocalDays(now, 1) - now));
    };
    // A sleeping machine holds timers back, and the zone can change while the page is open.
    const resync = () => {
      sync();
      schedule();
    };

    schedule();
    document.addEventListener('visibilitychange', resync);
    window.addEventListener('focus', resync);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', resync);
      window.removeEventListener('focus', resync);
    };
  }, []);

  return day;
}
