'use client';

import { useEffect, useMemo, useState } from 'react';
import { useDemoDataStore } from './store';
import { checkRestrictedQuestionAccess, type RestrictedQuestionAccess } from './restricted-question';

const RECHECK_MS = 15_000;
const RECHECK_LIMIT = 20;

/**
 * Whether the viewer can see the record behind the demo's restricted question,
 * so the chat landing can say up front that it will come back empty for them.
 * `null` until known, or when it cannot be told.
 */
export function useRestrictedQuestionAccess(): RestrictedQuestionAccess | null {
  const demoConnectors = useDemoDataStore((s) => s.demoConnectors);
  const idsKey = useMemo(
    () => demoConnectors.map((c) => c._key).filter((k): k is string => !!k).sort().join(','),
    [demoConnectors],
  );
  const [access, setAccess] = useState<RestrictedQuestionAccess | null>(null);

  useEffect(() => {
    const ids = idsKey ? idsKey.split(',') : [];
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let checks = 0;
    setAccess(null);
    // Not known yet, e.g. the demo is still syncing: look again, so the lock
    // appears once the record is in rather than never.
    const check = () => {
      checks += 1;
      void checkRestrictedQuestionAccess(ids).then((result) => {
        if (cancelled) return;
        setAccess(result);
        if (result === null && ids.length > 0 && checks < RECHECK_LIMIT) {
          timer = setTimeout(check, RECHECK_MS);
        }
      });
    };
    check();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [idsKey]);

  return access;
}
