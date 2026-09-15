import { ConnectorId } from '../libs/types/connector.types';

/**
 * Canonical Local FS connector key used across backend events/config.
 *
 * Local FS runs the same server-driven sync as every other connector: a
 * resync or scheduled tick reaches `run_sync`, which pulls file-event
 * metadata from the user's desktop through the desktop relay routes.
 */
export const LOCAL_FS_CONNECTOR_KEY = ConnectorId.LOCAL_FS as string;

export function isLocalFsConnector(connectorName: string): boolean {
  const normalized = connectorName
    .trim()
    .replace(/[_\s]+/g, '')
    .toLowerCase();
  return normalized === LOCAL_FS_CONNECTOR_KEY;
}
