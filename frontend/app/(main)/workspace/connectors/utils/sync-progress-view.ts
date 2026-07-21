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
