'use client';

import useSWR from 'swr';
import { SWR_POLICY } from '@/lib/api/swr-cache';
import { MetricsApi, ORG_KEYS, OrgApi } from './api';

/** The current org, read once per session; `OrgApi.updateOrg` refreshes it. */
export function useOrg() {
  return useSWR(ORG_KEYS.details, () => OrgApi.getOrg(), SWR_POLICY.sessionStable);
}

/** The org logo as a data URL (null when none), read once per session; logo writes update it. */
export function useOrgLogo() {
  return useSWR(ORG_KEYS.logo, () => OrgApi.getLogoUrl(), SWR_POLICY.sessionStable);
}

/** The metrics-collection setting (admins only: pass `enabled` false for others). */
export function useOrgMetrics(enabled: boolean) {
  return useSWR(enabled ? ORG_KEYS.metrics : null, () => MetricsApi.getMetricsCollection(), SWR_POLICY.sessionStable);
}
