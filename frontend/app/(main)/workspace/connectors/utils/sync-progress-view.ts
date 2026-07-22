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

/**
 * `label`/`detail` are English fallbacks; `labelKey`/`detailKey` (+ params)
 * point into the i18n catalogs so components render `t(labelKey, { defaultValue:
 * label, ...labelParams })`.
 */
interface Translatable {
  labelKey: string;
  labelParams?: Record<string, number>;
}

export type SyncProgressView =
  | ({ mode: 'discovering'; label: string; detail: string | null; detailKey?: string; detailParams?: Record<string, number> } & Translatable)
  | ({ mode: 'indexing'; label: string; percent: number } & Translatable)
  | ({ mode: 'settled'; label: string; failed: number; hasErrors: boolean } & Translatable)
  | ({ mode: 'failed'; label: string; failed: number } & Translatable)
  | ({ mode: 'deleting'; label: string } & Translatable)
  | { mode: 'none' };

const KEY_PREFIX = 'workspace.connectors.syncProgress';

/**
 * Reduce raw sync-progress + connector status into a single render decision.
 *
 * - DELETING -> "Removing…"
 * - DISCOVERING (or active with no run yet) -> indeterminate "Syncing source…"
 * - INDEXING -> determinate "Indexing X of Y"
 * - failed run -> "Sync failed"
 * - settled -> lifetime coverage ("Indexed", optionally "N failed")
 */
export function describeSyncProgress(
  progress: ConnectorSyncProgress | null | undefined,
  status?: string | null
): SyncProgressView {
  const normalizedStatus = (status ?? '').toUpperCase();
  if (normalizedStatus === CONNECTOR_INSTANCE_STATUS.DELETING) {
    return { mode: 'deleting', label: 'Removing…', labelKey: `${KEY_PREFIX}.removing` };
  }

  const active = Boolean(progress?.isActive) || isActiveConnectorSyncStatus(status);
  const run = progress?.run;
  // Checked after `active` so a stale failed run can't mask a sync that has
  // just been restarted (status flips to SYNCING before start_run resets Redis).
  if (!active && (run?.syncFailed || run?.phase === 'FAILED')) {
    return {
      mode: 'failed',
      label: 'Sync failed',
      labelKey: `${KEY_PREFIX}.syncFailed`,
      failed: run.failed ?? 0,
    };
  }

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
      return {
        mode: 'indexing',
        label: `Indexing ${processed} of ${total}`,
        labelKey: `${KEY_PREFIX}.indexingCount`,
        labelParams: { processed, total },
        percent,
      };
    }
    const isFullSync =
      normalizedStatus === CONNECTOR_INSTANCE_STATUS.FULL_SYNCING || Boolean(run?.fullSync);
    const discovered = run?.discovered ?? 0;
    return {
      mode: 'discovering',
      label: isFullSync ? 'Full sync - syncing source…' : 'Syncing source…',
      labelKey: isFullSync ? `${KEY_PREFIX}.fullSyncDiscovering` : `${KEY_PREFIX}.discovering`,
      detail: discovered > 0 ? `${discovered} queued` : null,
      detailKey: discovered > 0 ? `${KEY_PREFIX}.queuedCount` : undefined,
      detailParams: discovered > 0 ? { count: discovered } : undefined,
    };
  }

  const coverage = progress?.coverage;
  const total = coverage?.total ?? 0;
  const failed = coverage?.failed ?? 0;
  const pending = (coverage?.inProgress ?? 0) + (coverage?.queued ?? 0);
  if (pending > 0) {
    return {
      mode: 'indexing',
      label: `${pending} record${pending === 1 ? '' : 's'} still processing`,
      labelKey: `${KEY_PREFIX}.stillProcessing`,
      labelParams: { count: pending },
      percent: 0,
    };
  }
  if (total > 0) {
    return {
      mode: 'settled',
      label: 'Indexed',
      labelKey: `${KEY_PREFIX}.indexed`,
      failed,
      hasErrors: failed > 0,
    };
  }
  return { mode: 'none' };
}
