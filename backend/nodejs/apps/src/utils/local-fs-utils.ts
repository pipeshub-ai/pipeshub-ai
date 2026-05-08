import multer from 'multer';
import { ConnectorId } from '../libs/types/connector.types';

/**
 * Canonical Local FS connector key used across backend events/config.
 *
 * Local FS is **client-managed**: the desktop (Electron) app owns the file
 * watcher, the journal, and the scheduled rescan. The backend never crawls the
 * user's filesystem and never pushes a "sync now" command — it only ingests
 * events the desktop runtime POSTs. Server-side sync paths therefore short-
 * circuit when this returns true.
 */
export const LOCAL_FS_CONNECTOR_KEY = ConnectorId.LOCAL_FS as string;

export function isLocalFsConnector(connectorName: string): boolean {
  const normalized = connectorName
    .trim()
    .replace(/[_\s]+/g, '')
    .toLowerCase();
  return normalized === LOCAL_FS_CONNECTOR_KEY;
}

/** Max size per file for desktop Local FS `file-events/upload` multipart batches. */
export const LOCAL_FS_CONNECTOR_UPLOAD_MAX_FILE_BYTES = 100 * 1024 * 1024;

/** Max files per Local FS multipart upload request. */
export const LOCAL_FS_CONNECTOR_UPLOAD_MAX_FILES = 100;

/**
 * Multipart parser for `/instances/:connectorId/file-events/upload`.
 * Used by the desktop runtime when forwarding Local FS file batches to the Node API.
 */
export function createLocalFsConnectorUploadMulter(): multer.Multer {
  return multer({
    storage: multer.memoryStorage(),
    limits: {
      fileSize: LOCAL_FS_CONNECTOR_UPLOAD_MAX_FILE_BYTES,
      files: LOCAL_FS_CONNECTOR_UPLOAD_MAX_FILES,
    },
  });
}
