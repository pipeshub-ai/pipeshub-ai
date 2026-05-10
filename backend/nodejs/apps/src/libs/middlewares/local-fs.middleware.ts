import type { RequestHandler } from 'express';
import type { KeyValueStoreService } from '../services/keyValueStore.service';
import { getPlatformSettingsFromStore } from '../../modules/configuration_manager/utils/util';
import { KB_UPLOAD_LIMITS } from '../../modules/knowledge_base/constants/kb.constants';
import { createLocalFsConnectorUploadMulter } from '../../utils/local-fs-utils';

async function resolveLocalFsUploadMaxFileBytes(
  keyValueStoreService: KeyValueStoreService,
): Promise<number> {
  try {
    const settings = await getPlatformSettingsFromStore(keyValueStoreService);
    return settings.fileUploadMaxSizeBytes;
  } catch {
    return KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes;
  }
}

/**
 * Express middleware for `POST .../instances/:connectorId/file-events/upload`:
 * multipart parsing with per-file size from platform settings and max parts from KB upload limits.
 */
export function createLocalFsConnectorFileEventsUploadMiddleware(
  keyValueStoreService: KeyValueStoreService,
): RequestHandler {
  return (req, res, next) => {
    resolveLocalFsUploadMaxFileBytes(keyValueStoreService)
      .then((maxFileSizeBytes) => {
        void createLocalFsConnectorUploadMulter({
          maxFileSizeBytes,
          maxFiles: KB_UPLOAD_LIMITS.maxFilesPerRequest,
        }).any()(req, res, next);
      })
      .catch(next);
  };
}
