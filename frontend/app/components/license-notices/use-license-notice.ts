'use client';

import useSWR from 'swr';
import { apiClient } from '@/lib/api';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';

/** What GET /api/v1/license/notice answers (backend `LicenseNotice`, ee/modules/license/license-notice.ts). */
export interface LicenseNotice {
  /**
   * Set while no license key is in force and the upgrade grace runs. `key`: none installed (`missing`),
   * one dated ahead (`startsLater`, `keyDate` its start) or one that ended inside the grace (`ended`, `keyDate` its end).
   */
  upgradeGrace: { endsAt: string; key: 'missing' | 'startsLater' | 'ended'; keyDate: string | null } | null;
  permissionSync: boolean;
  notForProduction: boolean;
  connectors: {
    limited: number;
    restoring: number;
    /** Admins only; a member never gets the names. */
    items: ConnectorNotice[] | null;
  };
  /**
   * Admins only (null for a member): the seats of the license in force against the people using them
   * (backend `SeatLimit`, ee/modules/license/license-seats.ts). Null when nothing caps seats.
   */
  seatLimit: SeatLimitNotice | null;
}

export interface SeatLimitNotice {
  seats: number;
  used: number;
  /** How many `used` is above `seats`; 0 when within. */
  over: number;
  /** When new users start being refused; null when within. */
  refuseFrom: string | null;
  /** New users are refused now. */
  refusing: boolean;
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
 * list5m with a short dedupe window: a window as long as the poll makes every other tick reuse the last answer.
 * An install still refreshes at once (`revalidateSwrNamespace` drops the dedupe marker).
 */
export const LICENSE_NOTICE_POLICY = { ...SWR_POLICY.list5m, dedupingInterval: 30_000, refreshInterval: 300_000 };

/**
 * One request shared by every notice; refreshed every 5 minutes, as often as the server checks the license.
 * `enabled` false fetches nothing, for a notice that does not apply to this viewer.
 */
export function useLicenseNotice(enabled = true): LicenseNotice | undefined {
  const { data } = useSWR(enabled ? LICENSE_NOTICE_KEY : null, fetchNotice, LICENSE_NOTICE_POLICY);
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
