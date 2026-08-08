import { CONNECTOR_INSTANCE_STATUS } from '../constants';

export interface SyncConflictCopy {
  /** i18n key prefix under workspace.connectors.syncProgress.conflict.* */
  key: string;
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
}

const KEY_PREFIX = 'workspace.connectors.syncProgress.conflict';

/**
 * Phase-aware copy for the "a sync is already running" prompt. The four cases
 * differ because "restart" means different things: re-running the same op,
 * escalating a quick sync to a full sync, or downgrading a full sync (which we
 * gently discourage since full sync is the more thorough operation).
 *
 * The string fields are English fallbacks; render with
 * `t(`${copy.key}.title`, { defaultValue: copy.title })` etc.
 */
export function describeSyncConflict(
  currentStatus: string | undefined,
  requestedFullSync: boolean
): SyncConflictCopy {
  const fullRunning =
    (currentStatus ?? '').toUpperCase() === CONNECTOR_INSTANCE_STATUS.FULL_SYNCING;

  if (requestedFullSync && fullRunning) {
    return {
      key: `${KEY_PREFIX}.fullOverFull`,
      title: 'Full sync already running',
      message:
        'A full sync is already in progress. Restarting cancels the current one and re-indexes everything from scratch.',
      confirmLabel: 'Restart full sync',
      cancelLabel: 'Keep current sync',
    };
  }
  if (requestedFullSync && !fullRunning) {
    return {
      key: `${KEY_PREFIX}.fullOverQuick`,
      title: 'A sync is running',
      message:
        'A sync is currently in progress. Starting a full sync will cancel it and re-index everything from scratch.',
      confirmLabel: 'Cancel & full sync',
      cancelLabel: 'Keep current sync',
    };
  }
  if (!requestedFullSync && fullRunning) {
    return {
      key: `${KEY_PREFIX}.quickOverFull`,
      title: 'Full sync in progress',
      message:
        'A full sync is running and is more thorough than a quick sync. Running a quick sync now will cancel it — consider letting the full sync finish.',
      confirmLabel: 'Cancel & sync',
      cancelLabel: 'Keep full sync',
    };
  }
  return {
    key: `${KEY_PREFIX}.quickOverQuick`,
    title: 'Sync already running',
    message:
      'A sync is already in progress. Restarting cancels the current one and starts over.',
    confirmLabel: 'Restart sync',
    cancelLabel: 'Keep current sync',
  };
}
