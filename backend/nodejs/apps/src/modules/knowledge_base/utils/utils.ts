import FormData from 'form-data';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import axios from 'axios';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { endpoint } from '../../storage/constants/constants';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import { RecordRelationService } from '../services/kb.relation.service';
import { IRecordDocument } from '../types/record';
import { IFileRecordDocument } from '../types/file_record';
import { ConnectorServiceCommand } from '../../../libs/commands/connector_service/connector.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  INDEXING_STATUS,
  ORIGIN_TYPE,
  RECORD_TYPE,
} from '../constants/record.constants';
import {
  getFileExtension,
  getFilenameWithoutExtension,
} from '../../../libs/utils/file-extension.util';
import { mapWithConcurrency } from '../../../libs/utils/concurrency.util';

const logger = Logger.getInstance({
  service: 'knowledge_base.utils',
});

// Max storage uploads (and placeholder round-trips) in flight at once for a
// single batch. Bounds load on the storage service while ensuring one large
// file never serializes the whole batch behind it.
export const UPLOAD_STORAGE_CONCURRENCY = 5;

const axiosInstance = axios.create({
  maxRedirects: 0,
});

export interface StorageResponseMetadata {
  documentId: string;
  documentName: string;
}

export interface PlaceholderResult extends StorageResponseMetadata {
  // Lazy upload starter for the signed-URL (S3/Azure) path. Returning a thunk
  // instead of an already-running promise lets the caller bound how many
  // uploads run at once — the PUT only begins when `upload()` is invoked.
  upload?: () => Promise<void>;
  redirectUrl?: string;
}

/**
 * File metadata structure used during upload processing
 */
export interface FileUploadMetadata {
  file: FileBufferInfo;
  filePath: string;
  fileName: string;
  extension: string | null;
  correctMimeType: string;
  key: string;
  webUrl: string;
  validLastModified: number;
  size: number;
}

/**
 * Combined placeholder result with metadata for background processing
 */
export interface PlaceholderResultWithMetadata {
  placeholderResult: PlaceholderResult;
  metadata: FileUploadMetadata;
}

/**
 * Processed file structure sent to Python service
 */
export interface ProcessedFile {
  record: IRecordDocument;
  fileRecord: IFileRecordDocument;
  filePath: string;
  lastModified: number;
}

/**
 * Creates a placeholder document and returns metadata.
 * If a redirect URL is provided (for direct upload), returns an upload promise that must be awaited.
 * Event publishing is handled by Python service after all uploads complete.
 */
export const createPlaceholderDocument = async (
  req: AuthenticatedUserRequest,
  file: FileBufferInfo,
  documentName: string,
  isVersionedFile: boolean,
  keyValueStoreService: KeyValueStoreService,
  defaultConfig: DefaultStorageConfig,
  // Scoped STORAGE_TOKEN minted by the caller. We hit the INTERNAL storage route
  // (service-to-service) rather than the user-facing /upload route, so these
  // per-file byte transfers aren't gated by the user-facing API rate limiter and
  // the user route can stay locked down against abuse.
  storageToken: string,
): Promise<PlaceholderResult> => {
  const formData = new FormData();

  // Add the file with proper metadata
  formData.append('file', file.buffer, {
    filename: file.originalname,
    contentType: file.mimetype,
  });
  const url = (await keyValueStoreService.get<string>(endpoint)) || '{}';

  const storageUrl = JSON.parse(url).storage.endpoint || defaultConfig.endpoint;

  // Add other required fields
  formData.append(
    'documentPath',
    `PipesHub/KnowledgeBase/private/${req.user?.userId}`,
  );
  formData.append('isVersionedFile', isVersionedFile.toString());
  formData.append('documentName', getFilenameWithoutExtension(documentName));

  try {
    const response = await axiosInstance.post(
      `${storageUrl}/api/v1/document/internal/upload`,
      formData,
      {
        headers: {
          ...formData.getHeaders(),
          Authorization: `Bearer ${storageToken}`,
        },
      },
    );

    // Direct upload successful, no redirect needed
    return {
      documentId: response.data?._id,
      documentName: response.data?.documentName,
    };
  } catch (error: any) {
    if (error.response?.status === HTTP_STATUS.PERMANENT_REDIRECT) {
      const redirectUrl = error.response.headers.location;
      const documentId = error.response.headers['x-document-id'];
      const documentName = error.response.headers['x-document-name'];

      if (process.env.NODE_ENV == 'development') {
        logger.info('Placeholder created, upload required', {
          redirectUrl,
          documentId,
          documentName,
        });
      }

      // Return placeholder info with a LAZY upload starter. The caller invokes
      // `upload()` under a concurrency limit, so the byte transfer to the signed
      // URL only begins when a slot is free — not eagerly here (which would let
      // a whole batch's PUTs run unbounded).
      return {
        documentId,
        documentName,
        redirectUrl,
        upload: () =>
          uploadFileToSignedUrl(
            file.buffer,
            file.mimetype,
            redirectUrl,
            documentId,
            documentName,
          ),
      };
    } else {
      logger.error('Error creating placeholder document', {
        error: error.response?.data || error.message,
      });
      throw error;
    }
  }
};

/**
 * Uploads file buffer to a signed URL (S3/Azure direct upload) in background.
 * This function does NOT publish events - event publishing is handled by Python service.
 * Returns a promise that resolves when upload completes.
 */
export const uploadFileToSignedUrl = async (
  buffer: Buffer,
  _mimetype: string,
  redirectUrl: string,
  documentId: string,
  documentName: string,
): Promise<void> => {
  try {
    const response = await fetch(redirectUrl, {
      method: 'PUT',
      body: new Uint8Array(buffer),
      headers: {
        'Content-Length': buffer.length.toString(),
      },
    });

    if (response.ok) {
      logger.info('File uploaded to storage successfully', {
        documentId,
        documentName,
        status: response.status,
      });
    } else {
      const errorBody = await response.text();
      throw new Error(
        `Upload failed with status ${response.status}: ${errorBody}`,
      );
    }
  } catch (error: any) {
    logger.error('File upload to signed URL failed', {
      documentId,
      documentName,
      error: error.message,
      status: error.status,
    });
    throw error;
  }
};

/** Writes a single per-file outcome to the upload SSE response stream. */
export type UploadStreamPublish = (
  event: 'file:succeeded' | 'file:failed',
  data: Record<string, unknown>,
) => void;

/**
 * Uploads the accepted files to storage, creates their records via the indexing
 * service, and STREAMS a terminal per-file event for each (success or failure)
 * through `publish`. Returns the batch's succeeded/failed counts so the caller
 * can emit a final `done` summary. Runs inline within the streaming upload
 * request (no longer fire-and-forget).
 */
export const processUploadsInBackground = async (
  placeholderResults: PlaceholderResultWithMetadata[],
  orgId: string,
  currentTime: number,
  pythonServiceUrl: string,
  headers: Record<string, string>,
  logger: Logger,
  publish: UploadStreamPublish,
): Promise<{ succeeded: number; failed: number }> => {
  const uploadStartTime = Date.now();

  // Track successful and failed files separately
  const successfulResults: PlaceholderResultWithMetadata[] = [];
  const failedResults: Array<{
    result: PlaceholderResultWithMetadata;
    error: string;
  }> = [];

  // The file extension (lower-case, no dot) so the client can show the file
  // type / icon for a row even before the records list reloads.
  const extOf = (filePath: string, fileName: string): string | undefined =>
    getFileExtension(filePath) ?? getFileExtension(fileName) ?? undefined;

  const publishFailure = (
    meta: { key: string; fileName: string; filePath: string },
    message: string,
    stage: 'upload' | 'index',
    reason?: string,
  ): void => {
    publish('file:failed', {
      recordId: meta.key,
      fileName: meta.fileName,
      filePath: meta.filePath,
      extension: extOf(meta.filePath, meta.fileName),
      errors: [message],
      stage,
      ...(reason ? { reason } : {}),
    });
  };

  const publishSuccess = (meta: {
    key: string;
    fileName: string;
    filePath: string;
  }): void => {
    publish('file:succeeded', {
      recordId: meta.key,
      fileName: meta.fileName,
      filePath: meta.filePath,
      extension: extOf(meta.filePath, meta.fileName),
    });
  };

  // Batch outcome counts, returned to the caller for the final `done` summary.
  let batchSucceeded = 0;
  let batchFailed = 0;

  try {
    // STEP 1: Upload files to storage with BOUNDED CONCURRENCY. Previously this
    // ran strictly sequentially, so a single large file (e.g. 100 MB) stalled
    // every smaller file behind it. Now up to UPLOAD_STORAGE_CONCURRENCY uploads
    // run at once; the big file occupies one slot while the rest keep flowing.
    logger.info('Starting bounded-concurrency file uploads', {
      totalFiles: placeholderResults.length,
      uploadsRequired: placeholderResults.filter((r) => r.placeholderResult.upload).length,
      concurrency: UPLOAD_STORAGE_CONCURRENCY,
    });

    await mapWithConcurrency(
      placeholderResults,
      UPLOAD_STORAGE_CONCURRENCY,
      async (result) => {
        const { placeholderResult, metadata } = result;

        if (placeholderResult.upload) {
          try {
            await placeholderResult.upload();
            successfulResults.push(result);
            logger.debug('Background upload completed', {
              documentId: placeholderResult.documentId,
              documentName: placeholderResult.documentName,
              fileName: metadata.fileName,
            });
          } catch (uploadError: any) {
            failedResults.push({
              result,
              error: uploadError.message || 'Upload failed',
            });
            logger.error('Background upload failed', {
              documentId: placeholderResult.documentId,
              documentName: placeholderResult.documentName,
              fileName: metadata.fileName,
              error: uploadError.message,
              stack: uploadError.stack,
            });
            // Continue with other files even if one fails.
          }
        } else {
          // File was already uploaded directly (no redirect).
          successfulResults.push(result);
        }
      },
    );

    const uploadDuration = Date.now() - uploadStartTime;
    logger.info('All background uploads completed', {
      totalFiles: placeholderResults.length,
      successfulUploads: successfulResults.length,
      failedUploads: failedResults.length,
      durationMs: uploadDuration,
    });

    // Per-file storage-upload failures → stream as `file:failed` (stage upload).
    for (const fr of failedResults) {
      publishFailure(fr.result.metadata, fr.error, 'upload');
    }

    // Nothing to index if every upload failed.
    if (successfulResults.length === 0) {
      logger.warn('No successful uploads to process', {
        totalFiles: placeholderResults.length,
        failedFiles: failedResults.length,
      });
      batchFailed = failedResults.length;
      return { succeeded: batchSucceeded, failed: batchFailed };
    }

    // Build records with storage info using proper types - only for successful files
    const processedFiles: ProcessedFile[] = successfulResults.map((result) => {
      const { placeholderResult, metadata } = result;
      const { extension, correctMimeType, key, webUrl, validLastModified, size } = metadata;

      const record: IRecordDocument = {
        _key: key,
        orgId: orgId,
        recordName: placeholderResult.documentName,
        externalRecordId: placeholderResult.documentId,
        recordType: RECORD_TYPE.FILE,
        origin: ORIGIN_TYPE.UPLOAD,
        connectorId: `knowledgeBase_${orgId}`,
        createdAtTimestamp: currentTime,
        updatedAtTimestamp: currentTime,
        sourceCreatedAtTimestamp: validLastModified,
        sourceLastModifiedTimestamp: validLastModified,
        isDeleted: false,
        isArchived: false,
        indexingStatus: INDEXING_STATUS.QUEUED,
        version: 1,
        webUrl: webUrl,
        mimeType: correctMimeType,
        sizeInBytes: size,
      };

      const fileRecord: IFileRecordDocument = {
        _key: key,
        orgId: orgId,
        name: placeholderResult.documentName,
        isFile: true,
        extension: extension,
        mimeType: correctMimeType,
        sizeInBytes: size,
        webUrl: webUrl,
      };

      return {
        record,
        fileRecord,
        filePath: metadata.filePath,
        lastModified: validLastModified,
      };
    });

    // STEP 5: Call Python service with only successful files
    logger.info('Calling Python service with successful files', {
      successfulRecords: processedFiles.length,
      failedRecords: failedResults.length,
      pythonServiceUrl,
    });

    const connectorCommandOptions = {
      uri: pythonServiceUrl,
      method: HttpMethod.POST,
      headers: {
        ...headers,
        'Content-Type': 'application/json',
      },
      body: {
        files: processedFiles,
      },
    };

    const connectorCommand = new ConnectorServiceCommand(connectorCommandOptions);
    const response = await connectorCommand.execute();

    const totalDuration = Date.now() - uploadStartTime;

    if (response.statusCode === 200 || response.statusCode === 201) {
      // Python may partially fail: a 200 can still carry `failedFiles` (the
      // exact filePaths it could not create). Split the batch so we only report
      // the records that truly succeeded as processed, and report the rest as
      // failed — instead of claiming success for every file.
      const responseBody = (response.data ?? {}) as {
        failedFiles?: unknown;
        skippedFiles?: unknown;
        data?: { failedFiles?: unknown; skippedFiles?: unknown };
      };
      const failedPathList =
        (Array.isArray(responseBody.failedFiles) && responseBody.failedFiles) ||
        (Array.isArray(responseBody.data?.failedFiles) &&
          responseBody.data?.failedFiles) ||
        [];
      const failedPathSet = new Set(
        (failedPathList as unknown[]).filter(
          (p): p is string => typeof p === 'string' && p.length > 0,
        ),
      );

      // Files the indexing service SKIPPED because a record with the same name
      // already exists in the target KB/folder (or collided within the batch).
      // These are not errors but must be reported so the user knows they were
      // not added — otherwise they'd wrongly appear as succeeded.
      const skippedList =
        (Array.isArray(responseBody.skippedFiles) && responseBody.skippedFiles) ||
        (Array.isArray(responseBody.data?.skippedFiles) &&
          responseBody.data?.skippedFiles) ||
        [];
      const skippedPathSet = new Set(
        (skippedList as unknown[])
          .map((s) =>
            s && typeof s === 'object'
              ? (s as { filePath?: unknown }).filePath
              : s,
          )
          .filter((p): p is string => typeof p === 'string' && p.length > 0),
      );

      const pythonFailedFiles = processedFiles.filter((pf) =>
        failedPathSet.has(pf.filePath),
      );
      const pythonSkippedFiles = processedFiles.filter(
        (pf) => !failedPathSet.has(pf.filePath) && skippedPathSet.has(pf.filePath),
      );
      const succeededFiles = processedFiles.filter(
        (pf) =>
          !failedPathSet.has(pf.filePath) && !skippedPathSet.has(pf.filePath),
      );

      logger.info('Python service called successfully after uploads', {
        requestedRecords: processedFiles.length,
        indexedRecords: succeededFiles.length,
        serverFailedRecords: pythonFailedFiles.length,
        skippedRecords: pythonSkippedFiles.length,
        uploadFailedRecords: failedResults.length,
        statusCode: response.statusCode,
        totalDurationMs: totalDuration,
        uploadDurationMs: uploadDuration,
      });

      // Stream a terminal per-file event for every file in this batch: success
      // for the records that were created, failure for the ones the server
      // could not create, and a duplicate-name failure for the skipped ones.
      for (const pf of succeededFiles) {
        publishSuccess({
          key: pf.record._key,
          fileName: pf.record.recordName,
          filePath: pf.filePath,
        });
      }
      for (const pf of pythonFailedFiles) {
        publishFailure(
          {
            key: pf.record._key,
            fileName: pf.record.recordName,
            filePath: pf.filePath,
          },
          'Server failed to create this record',
          'index',
        );
      }
      for (const pf of pythonSkippedFiles) {
        publishFailure(
          {
            key: pf.record._key,
            fileName: pf.record.recordName,
            filePath: pf.filePath,
          },
          `A file named "${pf.record.recordName}" already exists in this location; it was skipped.`,
          'index',
          'DUPLICATE_NAME',
        );
      }

      batchSucceeded = succeededFiles.length;
      batchFailed =
        failedResults.length +
        pythonFailedFiles.length +
        pythonSkippedFiles.length;
    } else {
      logger.error('Python service call failed after uploads', {
        statusCode: response.statusCode,
        message: response.msg,
        successfulRecords: processedFiles.length,
        failedRecords: failedResults.length,
        totalDurationMs: totalDuration,
      });

      // Whole indexing request failed → every uploaded record failed (index).
      for (const pf of processedFiles) {
        publishFailure(
          {
            key: pf.record._key,
            fileName: pf.record.recordName,
            filePath: pf.filePath,
          },
          `Indexing service failed: ${response.msg || 'Unknown error'}`,
          'index',
        );
      }
      batchSucceeded = 0;
      batchFailed = failedResults.length + processedFiles.length;
    }
  } catch (error: any) {
    const totalDuration = Date.now() - uploadStartTime;
    logger.error('Background processing failed', {
      error: error.message,
      stack: error.stack,
      totalDurationMs: totalDuration,
      successfulUploads: successfulResults.length,
      failedUploads: failedResults.length,
    });

    // Catastrophic failure after uploads succeeded → mark the uploaded-but-
    // unindexed files as failed (storage-upload failures were already streamed).
    for (const result of successfulResults) {
      publishFailure(result.metadata, error.message || 'Processing failed', 'index');
    }
    batchSucceeded = 0;
    batchFailed = failedResults.length + successfulResults.length;
    // Don't rethrow — every file's outcome has already been streamed.
  }

  return { succeeded: batchSucceeded, failed: batchFailed };
};

// NOTE: getFilenameWithoutExtension now lives in libs/utils/file-extension.util.ts
// (canonical last-dot derivation, safe for names with no real extension).

/**
 * Legacy function for backward compatibility.
 * Creates placeholder and returns metadata.
 * For new code, use createPlaceholderDocument and handle uploads separately.
 */
export const saveFileToStorageAndGetDocumentId = async (
  req: AuthenticatedUserRequest,
  file: FileBufferInfo,
  documentName: string,
  isVersionedFile: boolean,
  _record: IRecordDocument,
  _fileRecord: IFileRecordDocument,
  keyValueStoreService: KeyValueStoreService,
  defaultConfig: DefaultStorageConfig,
  _recordRelationService: RecordRelationService,
  storageToken: string,
): Promise<StorageResponseMetadata> => {
  const result = await createPlaceholderDocument(
    req,
    file,
    documentName,
    isVersionedFile,
    keyValueStoreService,
    defaultConfig,
    storageToken,
  );

  // If a signed-URL upload is pending, start and await it (backward compat).
  if (result.upload) {
    await result.upload();
  }

  return {
    documentId: result.documentId,
    documentName: result.documentName,
  };
};

export const uploadNextVersionToStorage = async (
  _req: AuthenticatedUserRequest,
  file: FileBufferInfo,
  documentId: string,
  keyValueStoreService: KeyValueStoreService,
  defaultConfig: DefaultStorageConfig,
  // Scoped STORAGE_TOKEN — go through the INTERNAL storage route (see
  // createPlaceholderDocument) instead of forwarding the user's token.
  storageToken: string,
): Promise<StorageResponseMetadata> => {
  const formData = new FormData();

  // Add the file with proper metadata
  formData.append('file', file.buffer, {
    filename: file.originalname,
    contentType: file.mimetype,
  });

  const url = (await keyValueStoreService.get<string>(endpoint)) || '{}';

  const storageUrl = JSON.parse(url).storage.endpoint || defaultConfig.endpoint;

  try {
    const response = await axiosInstance.post(
      `${storageUrl}/api/v1/document/internal/${documentId}/uploadNextVersion`,
      formData,
      {
        headers: {
          ...formData.getHeaders(),
          Authorization: `Bearer ${storageToken}`,
        },
      },
    );

    return {
      documentId: response.data?._id,
      documentName: response.data?.documentName,
    };
  } catch (error: any) {
    logger.error('Error uploading file to storage', {
      documentId,
      error: error.response?.message || error.message,
      status: error.response?.status,
    });
    throw error;
  }
};
