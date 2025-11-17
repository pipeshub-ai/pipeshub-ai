import { Readable } from 'stream';
import FormData from 'form-data';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import axios from 'axios';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { endpoint } from '../../storage/constants/constants';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import { IRecordDocument } from '../types/record';
import { IFileRecordDocument } from '../types/file_record';
import { ConnectorServiceCommand } from '../../../libs/commands/connector_service/connector.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  INDEXING_STATUS,
  ORIGIN_TYPE,
  RECORD_TYPE,
} from '../constants/record.constants';

const logger = Logger.getInstance({
  service: 'knowledge_base.utils',
});

const axiosInstance = axios.create({
  maxRedirects: 0,
});

export interface StorageResponseMetadata {
  documentId: string;
  documentName: string;
}

export interface PlaceholderResult extends StorageResponseMetadata {
  uploadPromise?: Promise<void>;
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
      `${storageUrl}/api/v1/document/upload`,
      formData,
      {
        headers: {
          ...formData.getHeaders(),
          Authorization: req.headers.authorization,
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

      // Return placeholder info with upload promise
      // The upload will be handled separately and awaited before calling Python service
      return {
        documentId,
        documentName,
        redirectUrl,
        uploadPromise: uploadFileToSignedUrl(
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
  mimetype: string,
  redirectUrl: string,
  documentId: string,
  documentName: string,
): Promise<void> => {
  try {
    // Create a readable stream from the buffer
    const bufferStream = new Readable();
    bufferStream.push(buffer);
    bufferStream.push(null); // Signal end of stream

    const response = await axios({
      method: 'put',
      url: redirectUrl,
      data: bufferStream,
      headers: {
        'Content-Type': mimetype,
        'Content-Length': buffer.length,
      },
      maxContentLength: Infinity,
      maxBodyLength: Infinity,
    });

    if (response.status === 200 || response.status === 201) {
      logger.info('File uploaded to storage successfully', {
        documentId,
        documentName,
        status: response.status,
      });
    } else {
      throw new Error(`Unexpected status code: ${response.status}`);
    }
  } catch (error: any) {
    logger.error('File upload to signed URL failed', {
      documentId,
      documentName,
      error: error.message,
      status: error.response?.status,
    });
    throw error;
  }
};

/**
 * Processes uploads sequentially in background and calls Python service after completion.
 * This function does NOT publish events - Python service handles event publishing.
 * 
 * @param placeholderResults - Array of placeholder results with metadata
 * @param orgId - Organization ID
 * @param currentTime - Current timestamp
 * @param pythonServiceUrl - Python service endpoint URL
 * @param headers - HTTP headers to forward to Python service
 * @param logger - Logger instance
 * @returns Promise that resolves when background processing completes
 */
export const processUploadsInBackground = async (
  placeholderResults: PlaceholderResultWithMetadata[],
  orgId: string,
  userId: string,
  currentTime: number,
  pythonServiceUrl: string,
  headers: Record<string, string>,
  logger: Logger,
  notificationService?: any, // NotificationService - optional to avoid breaking existing code
): Promise<void> => {
  const uploadStartTime = Date.now();
  let successfulUploads = 0;
  let failedUploads = 0;

  try {
    // STEP 1: Upload all files sequentially (not parallel)
    logger.info('Starting sequential file uploads', {
      totalFiles: placeholderResults.length,
      uploadsRequired: placeholderResults.filter((r) => r.placeholderResult.uploadPromise).length,
    });

    for (const result of placeholderResults) {
      const { placeholderResult } = result;
      if (placeholderResult.uploadPromise) {
        try {
          await placeholderResult.uploadPromise;
          successfulUploads++;
          logger.debug('Background upload completed', {
            documentId: placeholderResult.documentId,
            documentName: placeholderResult.documentName,
          });
        } catch (uploadError: any) {
          failedUploads++;
          logger.error('Background upload failed', {
            documentId: placeholderResult.documentId,
            documentName: placeholderResult.documentName,
            error: uploadError.message,
            stack: uploadError.stack,
          });
          // Continue with other files even if one fails
        }
      } else {
        // File was already uploaded directly (no redirect)
        successfulUploads++;
      }
    }

    const uploadDuration = Date.now() - uploadStartTime;
    logger.info('All background uploads completed', {
      totalFiles: placeholderResults.length,
      successfulUploads,
      failedUploads,
      durationMs: uploadDuration,
    });

    // STEP 2: Build records with storage info using proper types
    const processedFiles: ProcessedFile[] = placeholderResults.map((result) => {
      const { placeholderResult, metadata } = result;
      const { extension, correctMimeType, key, webUrl, validLastModified, size } = metadata;

      const record: IRecordDocument = {
        _key: key,
        orgId: orgId,
        recordName: placeholderResult.documentName,
        externalRecordId: placeholderResult.documentId,
        recordType: RECORD_TYPE.FILE,
        origin: ORIGIN_TYPE.UPLOAD,
        createdAtTimestamp: currentTime,
        updatedAtTimestamp: currentTime,
        sourceCreatedAtTimestamp: validLastModified,
        sourceLastModifiedTimestamp: validLastModified,
        isDeleted: false,
        isArchived: false,
        indexingStatus: INDEXING_STATUS.NOT_STARTED,
        version: 1,
        webUrl: webUrl,
        mimeType: correctMimeType,
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

    // STEP 3: Call Python service (this will publish events)
    logger.info('Calling Python service with processed files', {
      totalRecords: processedFiles.length,
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
      logger.info('Python service called successfully after uploads', {
        totalRecords: processedFiles.length,
        statusCode: response.statusCode,
        totalDurationMs: totalDuration,
        uploadDurationMs: uploadDuration,
      });

      // Emit Socket.IO event to notify frontend that records are now processed
      if (notificationService) {
        const recordIds = processedFiles.map((pf) => pf.record._key);
        try {
          // Extract kbId and folderId from the URL
          const urlParts = pythonServiceUrl.split('/');
          const kbIdIndex = urlParts.findIndex((part) => part === 'kb') + 1;
          const kbId = kbIdIndex > 0 && kbIdIndex < urlParts.length ? urlParts[kbIdIndex] : undefined;
          const folderIdIndex = urlParts.findIndex((part) => part === 'folder');
          const folderId = folderIdIndex > 0 && folderIdIndex + 1 < urlParts.length ? urlParts[folderIdIndex + 1] : undefined;

          notificationService.sendToUser(userId, 'records:processed', {
            recordIds,
            orgId,
            kbId,
            folderId,
            totalRecords: processedFiles.length,
            timestamp: Date.now(),
          });
          logger.info('Socket.IO event emitted for processed records', {
            userId,
            recordIds: recordIds.slice(0, 3), // Log first 3 for debugging
            totalRecords: processedFiles.length,
          });
        } catch (socketError: any) {
          logger.error('Failed to emit Socket.IO event', {
            error: socketError.message,
            userId,
          });
          // Don't fail the upload if Socket.IO fails
        }
      }
    } else {
      logger.error('Python service call failed after uploads', {
        statusCode: response.statusCode,
        message: response.msg,
        totalRecords: processedFiles.length,
        totalDurationMs: totalDuration,
      });
      // Don't throw - background processing should be resilient
    }
  } catch (error: any) {
    const totalDuration = Date.now() - uploadStartTime;
    logger.error('Background processing failed', {
      error: error.message,
      stack: error.stack,
      totalDurationMs: totalDuration,
      successfulUploads,
      failedUploads,
    });
    // Don't throw - this is background processing, errors are logged but don't affect the response
  }
};

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
): Promise<StorageResponseMetadata> => {
  const result = await createPlaceholderDocument(
    req,
    file,
    documentName,
    isVersionedFile,
    keyValueStoreService,
    defaultConfig,
  );

  // If there's an upload promise, await it (for backward compatibility)
  if (result.uploadPromise) {
    await result.uploadPromise;
  }

  return {
    documentId: result.documentId,
    documentName: result.documentName,
  };
};

export const uploadNextVersionToStorage = async (
  req: AuthenticatedUserRequest,
  file: FileBufferInfo,
  documentId: string,
  keyValueStoreService: KeyValueStoreService,
  defaultConfig: DefaultStorageConfig,
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
      `${storageUrl}/api/v1/document/${documentId}/uploadNextVersion`,
      formData,
      {
        headers: {
          ...formData.getHeaders(),
          Authorization: req.headers.authorization,
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

function getFilenameWithoutExtension(originalname: string) {
  const fileExtension = originalname.slice(originalname.lastIndexOf('.') + 1);
  return originalname.slice(0, -fileExtension.length - 1);
}
