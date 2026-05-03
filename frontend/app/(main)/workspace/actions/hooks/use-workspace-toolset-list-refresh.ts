'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/** Second fetch after OAuth — list + auth chips can lag one read behind verify. */
const TOOLSET_LIST_REFOLLOW_MS = 550;

/**
 * Bump `refreshKey` immediately and once more after a short delay (OAuth / connector writes).
 * Used by workspace actions pages that load toolset lists.
 */
export function useWorkspaceToolsetListRefresh() {
  const [refreshKey, setRefreshKey] = useState(0);
  const listRefollowTimerRef = useRef<number | NodeJS.Timeout | null>(null);

  useEffect(
    () => () => {
      if (listRefollowTimerRef.current) {
        clearTimeout(listRefollowTimerRef.current);
        listRefollowTimerRef.current = null;
      }
    },
    []
  );

  const bumpRefreshKey = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  const refreshToolsetLists = useCallback(() => {
    bumpRefreshKey();
    if (listRefollowTimerRef.current) clearTimeout(listRefollowTimerRef.current);
    listRefollowTimerRef.current = window.setTimeout(() => {
      listRefollowTimerRef.current = null;
      bumpRefreshKey();
    }, TOOLSET_LIST_REFOLLOW_MS);
  }, [bumpRefreshKey]);

  return { refreshKey, bumpRefreshKey, refreshToolsetLists };
}
