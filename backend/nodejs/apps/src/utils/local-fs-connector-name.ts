/**
 * Canonical Local FS connector key used across backend events/config.
 *
 * Local FS is **client-managed**: the desktop (Electron) app owns the file
 * watcher, the journal, and the scheduled rescan. The backend never crawls the
 * user's filesystem and never pushes a "sync now" command — it only ingests
 * events the desktop runtime POSTs. Server-side sync paths therefore short-
 * circuit when this returns true.
 */
export const LOCAL_FS_CONNECTOR_KEY = 'localfs';

export function isLocalFsConnector(connectorName: string): boolean {
  const normalized = connectorName
    .trim()
    .replace(/[_\s]+/g, '')
    .toLowerCase();
  return normalized === LOCAL_FS_CONNECTOR_KEY;
}
