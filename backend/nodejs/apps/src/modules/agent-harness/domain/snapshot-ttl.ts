/**
 * OWD-12: engine sandbox snapshots are kept a short time unless the
 * deployment or the org shortens it further (`snapshots.ttlDays`, 7 days in
 * the release). A snapshot older than its TTL is never carried into a run
 * and the retention sweeper deletes it.
 */
import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';
import type { EffectiveRunConfig } from '../contracts/generated/core/policy/effective-run-config';

export const SNAPSHOT_TTL_DIMENSION = 'snapshots.ttlDays';
export const RELEASE_SNAPSHOT_TTL_DAYS: number =
  RELEASE_DEFAULTS.snapshots.ttlDays;
export const DAY_MS = 24 * 60 * 60 * 1000;

/** The TTL a resolved config gives; a config resolved before the row existed (or none) keeps the release's. */
export function snapshotTtlDays(
  config: EffectiveRunConfig | null | undefined,
): number {
  const value = config?.dimensions[SNAPSHOT_TTL_DIMENSION]?.value;
  return typeof value === 'number' && Number.isInteger(value) && value > 0
    ? value
    : RELEASE_SNAPSHOT_TTL_DAYS;
}

/** Snapshots taken before this time are expired. */
export const snapshotCutoff = (now: number, ttlDays: number): number =>
  now - ttlDays * DAY_MS;

/** Whether a snapshot journaled at `takenAt` (an envelope `ts`) is past `ttlDays`; an unreadable time is expired. */
export function snapshotExpired(
  takenAt: string,
  ttlDays: number,
  now: number,
): boolean {
  const at = Date.parse(takenAt);
  return Number.isNaN(at) || at < snapshotCutoff(now, ttlDays);
}
