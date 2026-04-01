import { folderSyncWatcherRegistry, FolderSyncResyncDispatchRequest } from '../../cli_rpc/socket/folder_sync_watcher_registry';

export function isFolderSyncConnector(connectorName: string): boolean {
  const normalized = connectorName
    .trim()
    .replace(/[_\s]+/g, '')
    .toLowerCase();
  return normalized === 'foldersync' || normalized === 'localfilesystem';
}

export class FolderSyncResyncDispatcher {
  async dispatch(request: FolderSyncResyncDispatchRequest): Promise<{
    replayedBatches: number;
    replayedEvents: number;
    skippedBatches: number;
  }> {
    return folderSyncWatcherRegistry.dispatch(request);
  }
}

export const folderSyncResyncDispatcher = new FolderSyncResyncDispatcher();
