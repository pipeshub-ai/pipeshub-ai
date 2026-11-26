import FormData from 'form-data';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import axios from 'axios';
import { installUpstreamLogging } from '../../../libs/http/upstream-call';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { STORAGE_WRITE_FAILED_MESSAGE } from '../../storage/constants/constants';
import { storedServiceEndpoint } from '../../storage/utils/service-endpoint';
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
import { handleBackendError } from '../../../libs/errors/backend-error';
import { withCause } from '../../../libs/errors/base.error';
import {
  classifyError,
  ErrorClass,
  levelFor,
} from '../../../libs/utils/error-class';

const logger = Logger.getInstance({
  service: 'knowledge_base.utils',
});

// Max storage uploads (and placeholder round-trips) in flight at once for a
// single batch. Bounds load on the storage service while ensuring one large
// file never serializes the whole batch behind it.
export const UPLOAD_STORAGE_CONCURRENCY = 5;

// Completes "PipesHub tried to ..." in the message a failed file carries.
const INDEX_OPERATION = 'add this file to the knowledge base';

const axiosInstance = axios.create({
  maxRedirects: 0,
});
// Created at import, possibly before index.ts instruments the default instance; a no-op if it inherited that.
installUpstreamLogging(axiosInstance);

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
 * The name the user picked, extension included. recordName has the extension
 * stripped, and names that differ only by extension may live side by side, so a
 * message naming recordName alone is ambiguous.
 */
export const displayFileName = (pf: ProcessedFile): string => {
  const base = pf.filePath.split(/[\\/]/).pop();
  if (base) return base;
  const ext = pf.fileRecord.extension;
  return ext ? `${pf.record.recordName}.${ext}` : pf.record.recordName;
};

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
  // Read inside the try, so a failed endpoint read is logged like a failed upload.
  let storageUrl: string;
  let created: { _id?: string; documentName?: string } | undefined;
  try {
    storageUrl = await storedServiceEndpoint(
      keyValueStoreService,
      'storage',
      defaultConfig.endpoint,
    );
    const formData = new FormData();
    formData.append('file', file.buffer, {
      filename: file.originalname,
      contentType: file.mimetype,
    });
    formData.append(
      'documentPath',
      `PipesHub/KnowledgeBase/private/${req.user?.userId}`,
    );
    formData.append('isVersionedFile', isVersionedFile.toString());
    formData.append('documentName', getFilenameWithoutExtension(documentName));

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
    created = response.data as typeof created;
  } catch (error: unknown) {
    // Storage answers a large file with a redirect to a signed URL, which axios
    // raises as an error.
    const redirect = axios.isAxiosError(error) ? error.response : undefined;
    if (redirect?.status === HTTP_STATUS.PERMANENT_REDIRECT) {
      const headers = redirect.headers as Record<string, unknown>;
      const redirectUrl: unknown = headers.location;
      const documentId: unknown = headers['x-document-id'];
      // Without both of these there is nowhere to send the file and nothing to
      // clean up afterwards, so treat the answer as a failed upload.
      if (
        typeof redirectUrl !== 'string' ||
        redirectUrl.trim() === '' ||
        typeof documentId !== 'string' ||
        documentId.trim() === ''
      ) {
        logger.error(
          'Storage asked for a direct upload but did not say where',
          {
            hasLocation:
              typeof redirectUrl === 'string' && redirectUrl.trim() !== '',
            hasDocumentId:
              typeof documentId === 'string' && documentId.trim() !== '',
          },
        );
        throw new Error(STORAGE_WRITE_FAILED_MESSAGE);
      }
      const docNameHeader = headers['x-document-name'];
      const rawDocName = typeof docNameHeader === 'string' ? docNameHeader : '';
      let documentName: string;
      try {
        documentName = decodeURIComponent(rawDocName);
      } catch {
        documentName = rawDocName;
      }

      // Return placeholder info with a LAZY upload starter. The caller invokes
      // `upload()` under a concurrency limit, so the byte transfer to the signed
      // URL only begins when a slot is free — not eagerly here (which would let
      // a whole batch's PUTs run unbounded).
      return {
        documentId,
        documentName,
        redirectUrl,
        upload: async () => {
          try {
            await uploadFileToSignedUrl(
              file.buffer,
              file.mimetype,
              redirectUrl,
              documentId,
            );
          } catch (uploadError: unknown) {
            // Storage removes the placeholder only if it confirms the file never arrived.
            await axiosInstance
              .post(
                `${storageUrl}/api/v1/document/internal/${documentId}/abortDirectUpload`,
                {},
                { headers: { Authorization: `Bearer ${storageToken}` } },
              )
              .catch((cleanupError: unknown) => {
                logger.warn(
                  'Could not remove the placeholder of a failed upload',
                  {
                    documentId: String(documentId),
                    error:
                      cleanupError instanceof Error
                        ? cleanupError.message
                        : String(cleanupError),
                  },
                );
              });
            // uploadFileToSignedUrl logged the refusal's status.
            throw withCause(
              new Error(STORAGE_WRITE_FAILED_MESSAGE),
              uploadError,
            );
          }
        },
      };
    } else {
      const errorClass = classifyError(error);
      logger[levelFor(errorClass)]('Error creating placeholder document', {
        status: axios.isAxiosError(error) ? error.response?.status : undefined,
        error_class: errorClass,
        // Our own failure (the endpoint read, a bug) needs its stack.
        ...(errorClass === 'internal' ? { error } : {}),
      });
      // Storage's own error body is written for the uploader; a refused
      // connection or a proxy's page would put our hosts on their screen.
      const storageMessage = axios.isAxiosError(error)
        ? (
            error.response?.data as
              | { error?: { message?: unknown } }
              | undefined
          )?.error?.message
        : undefined;
      if (typeof storageMessage === 'string' && storageMessage !== '') {
        throw error;
      }
      throw withCause(new Error(STORAGE_WRITE_FAILED_MESSAGE), error);
    }
  }
  if (!created?._id) {
    // Nothing to attach a record to, so the file is not saved as far as the
    // person uploading is concerned.
    logger.error('Storage saved the file but returned no document id');
    throw new Error(STORAGE_WRITE_FAILED_MESSAGE);
  }
  return {
    documentId: created._id,
    documentName: created.documentName ?? documentName,
  };
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
): Promise<void> => {
  let status: number | undefined;
  try {
    const response = await fetch(redirectUrl, {
      method: 'PUT',
      body: new Uint8Array(buffer),
      headers: {
        'Content-Length': buffer.length.toString(),
      },
    });
    status = response.status;
    if (!response.ok) {
      // The vendor's body quotes the signed request (bucket path, file name): it is never read.
      await response.body?.cancel();
      throw withCause(new Error('Upload to the signed URL was refused'), {
        status,
      });
    }
    logger.debug('File uploaded to storage successfully', {
      documentId,
      status,
    });
  } catch (error: unknown) {
    logger.warn('File upload to signed URL failed', {
      status,
      error_class: classifyError(error),
    });
    throw error;
  }
};

/** Why a batch's indexing step failed: sets the batch line's level. */
export interface UploadBatchFailure {
  status?: number;
  error_class: ErrorClass;
  error?: unknown;
}

/**
 * Writes an upload batch's one `kb.upload.batch.end` line from the totals of
 * its `done` summary: INFO, or the indexing failure's level.
 */
export const logUploadBatchEnd = (
  logger: Logger,
  totals: { files: number; succeeded: number; failed: number },
  startedAt: number,
  failure?: UploadBatchFailure,
): void => {
  logger[failure ? levelFor(failure.error_class) : 'info'](
    'kb.upload.batch.end',
    {
      outcome:
        totals.failed === 0
          ? 'success'
          : totals.succeeded > 0
            ? 'partial'
            : 'failure',
      ...totals,
      duration_ms: Date.now() - startedAt,
      ...failure,
    },
  );
};

/** Writes a single per-file outcome to the upload SSE response stream. */
export type UploadStreamPublish = (
  event: 'file:succeeded' | 'file:failed',
  data: Record<string, unknown>,
) => void;

/**
 * Uploads the accepted files to storage, creates their records via the indexing
 * service, and STREAMS a terminal per-file event for each (success or failure)
 * through `publish`. Returns the batch's succeeded/failed counts, and why
 * indexing failed when it did, so the caller can emit a final `done` summary
 * and the batch line. Runs inline within the streaming upload
 * request (no longer fire-and-forget).
 */
export const processUploadsInBackground = async (
  placeholderResults: PlaceholderResultWithMetadata[],
  orgId: string,
  kbId: string,
  currentTime: number,
  pythonServiceUrl: string,
  headers: Record<string, string>,
  logger: Logger,
  publish: UploadStreamPublish,
): Promise<{
  succeeded: number;
  failed: number;
  failure?: UploadBatchFailure;
}> => {
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
  let failure: UploadBatchFailure | undefined;

  try {
    // STEP 1: Upload files to storage with BOUNDED CONCURRENCY. Previously this
    // ran strictly sequentially, so a single large file (e.g. 100 MB) stalled
    // every smaller file behind it. Now up to UPLOAD_STORAGE_CONCURRENCY uploads
    // run at once; the big file occupies one slot while the rest keep flowing.
    await mapWithConcurrency(
      placeholderResults,
      UPLOAD_STORAGE_CONCURRENCY,
      async (result) => {
        const { placeholderResult } = result;

        if (placeholderResult.upload) {
          try {
            await placeholderResult.upload();
            successfulResults.push(result);
            logger.debug('Background upload completed', {
              documentId: placeholderResult.documentId,
            });
          } catch (uploadError: any) {
            failedResults.push({
              result,
              error: uploadError.message || 'Upload failed',
            });
            logger.debug('Background upload failed', {
              error_class: classifyError(uploadError),
            });
            // Continue with other files even if one fails.
          }
        } else {
          // File was already uploaded directly (no redirect).
          successfulResults.push(result);
        }
      },
    );

    // Per-file storage-upload failures → stream as `file:failed` (stage upload).
    for (const fr of failedResults) {
      publishFailure(fr.result.metadata, fr.error, 'upload');
    }

    // Nothing to index if every upload failed.
    if (successfulResults.length === 0) {
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
        connectorId: kbId,
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
          `A file named "${displayFileName(pf)}" already exists in this location; it was skipped.`,
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
      failure = {
        status: response.statusCode,
        error_class: classifyError(response.statusCode),
      };

      // Whole indexing request failed → every uploaded record failed (index).
      const indexFailure = handleBackendError(
        response,
        INDEX_OPERATION,
      ).message;
      for (const pf of processedFiles) {
        publishFailure(
          {
            key: pf.record._key,
            fileName: pf.record.recordName,
            filePath: pf.filePath,
          },
          indexFailure,
          'index',
        );
      }
      batchSucceeded = 0;
      batchFailed = failedResults.length + processedFiles.length;
    }
  } catch (error: any) {
    failure = { error_class: classifyError(error), error };

    // Catastrophic failure after uploads succeeded → mark the uploaded-but-
    // unindexed files as failed (storage-upload failures were already streamed).
    const indexFailure = handleBackendError(error, INDEX_OPERATION).message;
    for (const result of successfulResults) {
      publishFailure(result.metadata, indexFailure, 'index');
    }
    batchSucceeded = 0;
    batchFailed = failedResults.length + successfulResults.length;
    // Don't rethrow — every file's outcome has already been streamed.
  }

  return failure
    ? { succeeded: batchSucceeded, failed: batchFailed, failure }
    : { succeeded: batchSucceeded, failed: batchFailed };
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

  const storageUrl = await storedServiceEndpoint(
    keyValueStoreService,
    'storage',
    defaultConfig.endpoint,
  );

  // A failure propagates unlogged: its call line has the status, and the caller reports it once.
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
};
