'use client';

import { useCallback, type MutableRefObject } from 'react';
import { isElectron } from '@/lib/electron';
import { useConnectorsStore } from '../store';
import { isLocalFsConnectorType } from './local-fs-helpers';
import {
  buildLocalSyncStartOptionsFromConnectorConfig,
  extractLocalFsRootPath,
  startElectronLocalSync,
  stopElectronLocalSync,
  getElectronLocalSyncStatus,
} from './electron-local-sync';
import type { ConnectorConfig, ConnectorInstance } from '../types';

export type EnsureLocalWatcherFn = (
  instance: ConnectorInstance,
  config?: ConnectorConfig | null
) => Promise<void>;

/**
 * Reconciles the Electron local-sync watcher for a single connector instance
 * with the latest backend state: stop and clear status once it stops being
 * active+configured+authed, otherwise keep it mounted.
 *
 * Mounting is unconditional for an active instance. The watcher no longer
 * decides *when* to sync — the server does — so its only job is keeping the
 * journal current, and leaving it down just makes the next incremental pull
 * fall back to a full rescan.
 *
 * `managedWatcherIdsRef` is tracked by the caller so the page can stop
 * watchers for instances that disappear from the active list.
 */
export function useEnsureLocalWatcher(
  managedWatcherIdsRef: MutableRefObject<Set<string>>
): EnsureLocalWatcherFn {
  const setLocalSyncStatus = useConnectorsStore((s) => s.setLocalSyncStatus);
  const clearLocalSyncStatus = useConnectorsStore((s) => s.clearLocalSyncStatus);

  return useCallback(
    async (instance, config) => {
      if (!instance._key) return;
      if (!isElectron()) return;
      if (!isLocalFsConnectorType(instance.type)) return;
      if (!instance.isActive || !instance.isConfigured || !instance.isAuthenticated) {
        await stopElectronLocalSync(instance._key);
        managedWatcherIdsRef.current.delete(instance._key);
        clearLocalSyncStatus(instance._key);
        return;
      }
      const rootPath = extractLocalFsRootPath(config);
      if (!rootPath) return;

      await startElectronLocalSync({
        connectorId: instance._key,
        connectorName: instance.name,
        rootPath,
        ...buildLocalSyncStartOptionsFromConnectorConfig(config, instance.type),
      });
      const status = await getElectronLocalSyncStatus(instance._key);
      if (status) {
        setLocalSyncStatus(instance._key, status);
        managedWatcherIdsRef.current.add(instance._key);
      }
    },
    [setLocalSyncStatus, clearLocalSyncStatus, managedWatcherIdsRef]
  );
}
