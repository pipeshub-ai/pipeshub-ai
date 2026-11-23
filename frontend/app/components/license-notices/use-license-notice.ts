'use client';

import useSWR from 'swr';
import { apiClient } from '@/lib/api';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';

/** What GET /api/v1/license/notice answers (backend `LicenseNotice`, ee/modules/license/license-notice.ts). */
export interface LicenseNotice {
  /** Set while no license key has started and the upgrade grace runs. */
  upgradeGrace: { endsAt: string } | null;
  permissionSync: boolean;
  notForProduction: boolean;
  connectors: {
    limited: number;
    restoring: number;
    /** Admins only; a member never gets the names. */
    items: ConnectorNotice[] | null;
  };
}

export interface ConnectorNotice {
  id: string;
  name: string;
  type: string;
  state: 'affected' | 'limited' | 'restoring';
  /** Sync status: QUEUED, SYNCING, FULL_SYNCING, IDLE, ... */
  status: string | null;
}

export const LICENSE_NOTICE_KEY = [SWR_NS.licenseNotice] as const;

const fetchNotice = async (): Promise<LicenseNotice> =>
  (await apiClient.get<LicenseNotice>('/api/v1/license/notice', { suppressErrorToast: true })).data;

/**
 * One request shared by every notice; refreshed every 5 minutes, as often as the server checks the license.
 * `enabled` false fetches nothing, for a notice that does not apply to this viewer.
 */
export function useLicenseNotice(enabled = true): LicenseNotice | undefined {
  const { data } = useSWR(enabled ? LICENSE_NOTICE_KEY : null, fetchNotice, { ...SWR_POLICY.list5m, refreshInterval: 300_000 });
  return data;
}

const DAY_MS = 24 * 60 * 60 * 1000;

export const daysUntil = (iso: string, now: number = Date.now()): number =>
  Math.max(0, Math.ceil((Date.parse(iso) - now) / DAY_MS));

/** The days left at which the grace banner comes back after it was dismissed. */
export const REMINDER_DAYS = [14, 7, 3, 1] as const;

/** Which reminder a countdown is in: the last threshold reached, or 'start' above the first one. */
export function reminderStep(daysLeft: number): string {
  const reached = REMINDER_DAYS.filter((days) => daysLeft <= days);
  return reached.length ? String(reached[reached.length - 1]) : 'start';
}
