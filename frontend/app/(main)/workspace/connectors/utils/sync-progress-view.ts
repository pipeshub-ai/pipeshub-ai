import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import type { ConnectorSyncProgress } from '../types';

/** True while the connector instance status reflects an in-flight source sync. */
export function isActiveConnectorSyncStatus(status?: string | null): boolean {
  const normalized = (status ?? '').toUpperCase();
  return (
    normalized === CONNECTOR_INSTANCE_STATUS.SYNCING ||
    normalized === CONNECTOR_INSTANCE_STATUS.FULL_SYNCING
  );
}

export type SyncProgressView =
  | { mode: 'discovering'; label: string; detail: string | null }
  | { mode: 'indexing'; label: string; percent: number }
  | { mode: 'settled'; label: string; failed: number; hasErrors: boolean }
  | { mode: 'deleting'; label: string }
  | { mode: 'none' };

/**
 * Reduce raw sync-progress + connector status into a single render decision.
 *
 * - DELETING -> "Removing…"
 * - DISCOVERING (or active with no run yet) -> indeterminate "Syncing source…"
 * - INDEXING -> determinate "Indexing X of Y"
 * - settled -> lifetime coverage ("Indexed", optionally "N failed")
 */
export function describeSyncProgress(
  progress: ConnectorSyncProgress | null | undefined,
  status?: string | null
): SyncProgressView {
  const normalizedStatus = (status ?? '').toUpperCase();
  if (normalizedStatus === CONNECTOR_INSTANCE_STATUS.DELETING) {
    return { mode: 'deleting', label: 'Removing…' };
  }

  const active = Boolean(progress?.isActive) || isActiveConnectorSyncStatus(status);
  const run = progress?.run;

  if (active) {
    if (run && run.phase === 'INDEXING') {
      const total = run.total || 0;
      const processed = total > 0 ? Math.min(run.processed || 0, total) : run.processed || 0;
      const percent =
        run.percent != null
          ? run.percent
          : total > 0
            ? Math.min(100, Math.round((processed / total) * 100))
            : 0;
      return { mode: 'indexing', label: `Indexing ${processed} of ${total}`, percent };
    }
    const isFullSync =
      normalizedStatus === CONNECTOR_INSTANCE_STATUS.FULL_SYNCING || Boolean(run?.fullSync);
    const discovered = run?.discovered ?? 0;
    return {
      mode: 'discovering',
      label: isFullSync ? 'Full sync - syncing source…' : 'Syncing source…',
      detail: discovered > 0 ? `${discovered} queued` : null,
    };
  }

  const coverage = progress?.coverage;
  const total = coverage?.total ?? 0;
  const failed = coverage?.failed ?? 0;
  if (total > 0) {
    return { mode: 'settled', label: 'Indexed', failed, hasErrors: failed > 0 };
  }
  return { mode: 'none' };
}

/** Minimal instance shape the catalog activity tracker needs. */
export interface SyncActivityInstance {
  id: string;
  type: string;
  status?: string | null;
}

/**
 * Given the tracked instances, their fetched run-active flags, and the prior
 * tracked id set, decide which instances are still actively syncing/indexing.
 *
 * An instance stays "active" while its status is an in-flight sync OR its run
 * is still active (the indexing phase after discovery closed and status → IDLE).
 * Instances that have settled — or that no longer exist — are dropped so polling
 * winds down.
 */
export function reconcileSyncActivity(
  instances: SyncActivityInstance[],
  runActiveById: Record<string, boolean>,
  trackedIds: string[]
): { activeIdToType: Record<string, string>; trackedIds: string[] } {
  const byId = new Map(instances.map((i) => [i.id, i]));
  const tracked = new Set(trackedIds);
  for (const i of instances) {
    if (i.id && isActiveConnectorSyncStatus(i.status)) {
      tracked.add(i.id);
    }
  }

  const activeIdToType: Record<string, string> = {};
  for (const id of Array.from(tracked)) {
    const inst = byId.get(id);
    if (!inst) {
      tracked.delete(id);
      continue;
    }
    const active = Boolean(runActiveById[id]) || isActiveConnectorSyncStatus(inst.status);
    if (active) {
      activeIdToType[id] = inst.type;
    } else {
      tracked.delete(id);
    }
  }

  return { activeIdToType, trackedIds: Array.from(tracked) };
}

/** Count active instances per connector type. */
export function countByType(activeIdToType: Record<string, string>): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const type of Object.values(activeIdToType)) {
    if (type) {
      counts[type] = (counts[type] || 0) + 1;
    }
  }
  return counts;
}
