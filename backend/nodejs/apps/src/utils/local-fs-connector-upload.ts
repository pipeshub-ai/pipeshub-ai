import multer from 'multer';

/** Max size per file for desktop Local FS `file-events/upload` multipart batches. */
export const LOCAL_FS_CONNECTOR_UPLOAD_MAX_FILE_BYTES = 1024 * 1024 * 300;

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
