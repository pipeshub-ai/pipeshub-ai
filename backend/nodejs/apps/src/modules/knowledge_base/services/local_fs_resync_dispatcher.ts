import {
  localFsWatcherRegistry,
  LocalFsResyncDispatchRequest,
} from '../../cli_rpc/socket/local_fs_watcher_registry';

export function isLocalFsConnector(connectorName: string): boolean {
  const normalized = connectorName
    .trim()
    .replace(/[_\s]+/g, '')
    .toLowerCase();
  return (
    normalized === 'foldersync' ||
    normalized === 'localfilesystem' ||
    normalized === 'localfs'
  );
}

export class LocalFsResyncDispatcher {
  async dispatch(request: LocalFsResyncDispatchRequest): Promise<{
    replayedBatches: number;
    replayedEvents: number;
    skippedBatches: number;
  }> {
    return localFsWatcherRegistry.dispatch(request);
  }
}

export const localFsResyncDispatcher = new LocalFsResyncDispatcher();
