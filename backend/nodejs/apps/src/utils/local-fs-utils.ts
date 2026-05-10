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

/** Limits for desktop Local FS `file-events/upload` multipart batches (Multer). */
export interface LocalFsConnectorUploadLimits {
  /** Per-part max size in bytes — should match platform `fileUploadMaxSizeBytes`. */
  maxFileSizeBytes: number;
  /** Max file parts per request (callers typically pass `KB_UPLOAD_LIMITS.maxFilesPerRequest`). */
  maxFiles: number;
}

/**
 * Multipart parser for `/instances/:connectorId/file-events/upload`.
 * Used by the desktop runtime when forwarding Local FS file batches to the Node API.
 * Callers supply limits (often from platform settings and shared KB upload caps); the connector
 * upload route uses `createLocalFsConnectorFileEventsUploadMiddleware` in `local-fs.middleware`.
 */
export function createLocalFsConnectorUploadMulter(
  limits: LocalFsConnectorUploadLimits,
): multer.Multer {
  return multer({
    storage: multer.memoryStorage(),
    limits: {
      fileSize: limits.maxFileSizeBytes,
      files: limits.maxFiles,
    },
  });
}
