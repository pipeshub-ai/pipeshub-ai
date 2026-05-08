'use client';

import { useCallback, type MutableRefObject } from 'react';
import { isElectron } from '@/lib/electron';
import { useConnectorsStore } from '../store';
import { isLocalFsConnectorType } from './local-fs-helpers';
import {
  buildLocalFsWatcherOptionsFromConnectorConfig,
  buildLocalSyncScheduleFromConnectorConfig,
  extractLocalFsRootPath,
  startElectronLocalSync,
  stopElectronLocalSync,
  getElectronLocalSyncStatus,
  replayElectronLocalSync,
} from './electron-local-sync';
import type { ConnectorConfig, ConnectorInstance } from '../types';

export type EnsureLocalWatcherFn = (
  instance: ConnectorInstance,
  config?: ConnectorConfig | null
) => Promise<void>;

/**
 * Reconciles the Electron local-sync watcher for a single connector instance
 * with the latest backend state — start/replay when active+configured+authed,
 * stop and clear status otherwise. Shared between personal + team pages so
 * both surfaces manage their watchers identically.
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

      const schedulePayload = buildLocalSyncScheduleFromConnectorConfig(config, instance.type);
      await startElectronLocalSync({
        connectorId: instance._key,
        connectorName: instance.name,
        rootPath,
        ...buildLocalFsWatcherOptionsFromConnectorConfig(config),
        ...schedulePayload,
      });
      // SCHEDULED: edits stay journaled until the next tick; draining here on
      // every card-open / list refresh would defeat the user-configured
      // interval. Init() at app boot and the scheduled tick itself drain the
      // journal in that mode.
      if (schedulePayload.syncStrategy !== 'SCHEDULED') {
        await replayElectronLocalSync(instance._key);
      }
      const status = await getElectronLocalSyncStatus(instance._key);
      if (status) {
        setLocalSyncStatus(instance._key, status);
        managedWatcherIdsRef.current.add(instance._key);
      }
    },
    [setLocalSyncStatus, clearLocalSyncStatus, managedWatcherIdsRef]
  );
}
