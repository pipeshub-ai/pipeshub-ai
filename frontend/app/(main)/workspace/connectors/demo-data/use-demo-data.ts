'use client';

import { useCallback, useEffect, useState } from 'react';
import useSWR from 'swr';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import { ACTIVE_TEAM_CONNECTORS_KEY, fetchActiveTeamConnectors } from '../cache-keys';
import { useDemoDataStore } from './store';
import type { DemoDataStatus } from './api';
import { hasActiveDemo, isRemovalNoticeSnoozed, snoozeRemovalNotice } from './demo-data';

// SWR only decides when to read the switch again; the answer lives in the store,
// next to the guard that drops a read overtaken by a change, so the cached data
// is just `true`. It follows the connectors, so connector writes refresh it.
const DEMO_STATUS_KEY = [SWR_NS.teamConnectorLists, 'demo-status'] as const;

/**
 * True when the org has an enabled Demo connector (the Acme Corp sample
 * company). Looks it up at most once a minute; call it once per page, not per item.
 */
export function useDemoDataActive(): boolean {
  const active = useDemoDataStore((s) => hasActiveDemo(s.demoConnectors));
  const setTeamConnectors = useDemoDataStore((s) => s.setTeamConnectors);
  // Read from `data`, not onSuccess: the connectors page may be the reader that
  // fetched the list, and onSuccess runs only in that reader.
  const { data } = useSWR(ACTIVE_TEAM_CONNECTORS_KEY, fetchActiveTeamConnectors, SWR_POLICY.list60s);
  useEffect(() => {
    if (data) setTeamConnectors(data.connectors ?? []);
  }, [data, setTeamConnectors]);
  return active;
}

/**
 * Whether a record from `connectorId` is Acme Corp sample data. Reads what the
 * page already looked up, so it is cheap to call for every citation.
 */
export function useIsDemoSource(connectorId: string | null | undefined): boolean {
  return useDemoDataStore(
    (s) => !!connectorId && s.demoConnectors.some((c) => c._key === connectorId),
  );
}

/**
 * Whether to offer removing the demo data: to an admin, while it exists
 * (enabled or not, since its records stay searchable), once the company's own
 * records are indexed — from a connector or uploaded to a Collection. That is
 * the point where answers start mixing Acme Corp with real data.
 */
export function useDemoRemovalNotice(isAdmin: boolean | null) {
  const demoConnectors = useDemoDataStore((s) => s.demoConnectors);
  const realDataIndexed = useDemoDataStore((s) => s.realDataIndexed);
  const checkRealData = useDemoDataStore((s) => s.checkRealData);
  const [, setSnoozeTick] = useState(0);

  const hasDemo = demoConnectors.length > 0;
  const noticeKey = demoConnectors[0]?._key;

  useEffect(() => {
    if (isAdmin === true && hasDemo) void checkRealData();
  }, [isAdmin, hasDemo, checkRealData]);

  const snooze = useCallback(() => {
    if (noticeKey) snoozeRemovalNotice(noticeKey);
    setSnoozeTick((n) => n + 1);
  }, [noticeKey]);

  const show =
    isAdmin === true &&
    hasDemo &&
    realDataIndexed === true &&
    !!noticeKey &&
    !isRemovalNoticeSnoozed(noticeKey);

  return { show, demoConnectors, snooze };
}

/**
 * This person's demo data switch, read at most once a minute. `null` until known;
 * treat that as "as before" (shown) rather than guessing.
 */
export function useDemoDataStatus(): DemoDataStatus | null {
  const status = useDemoDataStore((s) => s.status);
  const load = useDemoDataStore((s) => s.loadStatus);
  useSWR(DEMO_STATUS_KEY, () => load().then(() => true), SWR_POLICY.list60s);
  return status;
}
