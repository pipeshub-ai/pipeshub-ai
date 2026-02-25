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
import { NotificationService } from '../../notification/service/notification.service';

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
 * This function tracks successful and failed files separately and only sends successful files to Python API.
 * 
 * @param placeholderResults - Array of placeholder results with metadata
 * @param orgId - Organization ID
 * @param userId - User ID for notifications
 * @param currentTime - Current timestamp
 * @param pythonServiceUrl - Python service endpoint URL
 * @param headers - HTTP headers to forward to Python service
 * @param logger - Logger instance
 * @param notificationService - NotificationService for sending socket events
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
  notificationService?: NotificationService,
  kbId?: string,
  folderId?: string,
): Promise<void> => {
  const uploadStartTime = Date.now();
  
  // Track successful and failed files separately
  const successfulResults: PlaceholderResultWithMetadata[] = [];
  const failedResults: Array<{
    result: PlaceholderResultWithMetadata;
    error: string;
  }> = [];

  try {
    // STEP 1: Upload all files sequentially (not parallel)
    logger.info('Starting sequential file uploads', {
      totalFiles: placeholderResults.length,
      uploadsRequired: placeholderResults.filter((r) => r.placeholderResult.uploadPromise).length,
    });

    for (const result of placeholderResults) {
      const { placeholderResult, metadata } = result;
      
      if (placeholderResult.uploadPromise) {
        try {
          await placeholderResult.uploadPromise;
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
          // Continue with other files even if one fails
        }
      } else {
        // File was already uploaded directly (no redirect)
        successfulResults.push(result);
      }
    }

    const uploadDuration = Date.now() - uploadStartTime;
    logger.info('All background uploads completed', {
      totalFiles: placeholderResults.length,
      successfulUploads: successfulResults.length,
      failedUploads: failedResults.length,
      durationMs: uploadDuration,
    });

    // STEP 2: Use provided kbId and folderId, or extract from URL as fallback
    // Prefer explicit parameters over URL parsing for better reliability
    const finalKbId = kbId || (() => {
      const urlParts = pythonServiceUrl.split('/');
      const kbIdIndex = urlParts.findIndex((part) => part === 'kb') + 1;
      return kbIdIndex > 0 && kbIdIndex < urlParts.length ? urlParts[kbIdIndex] : undefined;
    })();
    const finalFolderId = folderId || (() => {
      const urlParts = pythonServiceUrl.split('/');
      const folderIdIndex = urlParts.findIndex((part) => part === 'folder');
      return folderIdIndex > 0 && folderIdIndex + 1 < urlParts.length ? urlParts[folderIdIndex + 1] : undefined;
    })();

    // STEP 3: Send failed files notification if any
    // Event-driven: Send immediately when failures are detected, no delays
    // NotificationService handles connection state and queuing automatically
    if (failedResults.length > 0 && notificationService) {
      try {
        const failedFiles = failedResults.map((fr) => ({
          recordId: fr.result.metadata.key,
          fileName: fr.result.metadata.fileName,
          filePath: fr.result.metadata.filePath,
          error: fr.error,
        }));

        const eventData = {
          failedFiles,
          orgId,
          kbId: finalKbId,
          folderId: finalFolderId,
          totalFailed: failedResults.length,
          timestamp: Date.now(),
        };

        // Send immediately - service handles connection state and queuing
        const sentImmediately = notificationService.sendToUser(userId, 'records:failed', eventData);

        logger.info('Notification sent for failed records', {
          userId,
          totalFailed: failedResults.length,
          kbId: finalKbId,
          folderId: finalFolderId,
          sentImmediately,
        });
      } catch (socketError: unknown) {
        const error = socketError instanceof Error ? socketError : new Error(String(socketError));
        logger.error('Failed to send notification for failed files', {
          error: error.message,
          stack: error.stack,
          userId,
          kbId: finalKbId,
          folderId: finalFolderId,
        });
        // Don't fail the upload if notification fails - this is a non-critical notification
      }
    }

    // STEP 4: Process successful files - only send successful files to Python API
    if (successfulResults.length === 0) {
      logger.warn('No successful uploads to process', {
        totalFiles: placeholderResults.length,
        failedFiles: failedResults.length,
      });
      return;
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
      logger.info('Python service called successfully after uploads', {
        successfulRecords: processedFiles.length,
        failedRecords: failedResults.length,
        statusCode: response.statusCode,
        totalDurationMs: totalDuration,
        uploadDurationMs: uploadDuration,
      });

      // STEP 6: Emit Socket.IO event to notify frontend that successful records are now processed
      // Event-driven: Send immediately when processing completes, no delays
      // NotificationService handles connection state and queuing automatically
      if (notificationService) {
        const recordIds = processedFiles.map((pf) => pf.record._key);
        try {
          const eventData = {
            recordIds,
            orgId,
            kbId: finalKbId,
            folderId: finalFolderId,
            totalRecords: processedFiles.length,
            timestamp: Date.now(),
          };

          // Send immediately - service handles connection state and queuing
          const sentImmediately = notificationService.sendToUser(userId, 'records:processed', eventData);
          
          logger.info('Notification sent for processed records', {
            userId,
            recordIds: recordIds.slice(0, 3),
            totalRecords: processedFiles.length,
            kbId: finalKbId,
            folderId: finalFolderId,
            sentImmediately,
            totalDurationMs: totalDuration,
            uploadDurationMs: uploadDuration,
          });
        } catch (socketError: unknown) {
          const error = socketError instanceof Error ? socketError : new Error(String(socketError));
          logger.error('Failed to send notification for processed records', {
            error: error.message,
            stack: error.stack,
            userId,
            kbId: finalKbId,
            folderId: finalFolderId,
            totalRecords: processedFiles.length,
          });
          // Don't fail the upload if notification fails - this is a non-critical notification
        }
      } else {
        logger.warn('NotificationService not available - socket events will not be sent', {
          userId,
          kbId: finalKbId,
          folderId: finalFolderId,
          totalRecords: processedFiles.length,
        });
      }
    } else {
      logger.error('Python service call failed after uploads', {
        statusCode: response.statusCode,
        message: response.msg,
        successfulRecords: processedFiles.length,
        failedRecords: failedResults.length,
        totalDurationMs: totalDuration,
      });
      
      // If Python API fails, mark all successful uploads as failed in notification
      // Event-driven: Send immediately when API failure is detected
      if (notificationService && processedFiles.length > 0) {
        try {
          const failedFiles = processedFiles.map((pf) => ({
            recordId: pf.record._key,
            fileName: pf.record.recordName,
            filePath: pf.filePath,
            error: `Python API call failed: ${response.msg || 'Unknown error'}`,
          }));

          const eventData = {
            failedFiles,
            orgId,
            kbId: finalKbId,
            folderId: finalFolderId,
            totalFailed: processedFiles.length,
            timestamp: Date.now(),
          };

          // Send immediately - service handles connection state and queuing
          const sentImmediately = notificationService.sendToUser(userId, 'records:failed', eventData);

          logger.info('Notification sent for Python API failures', {
            userId,
            kbId: finalKbId,
            folderId: finalFolderId,
            totalFailed: processedFiles.length,
            sentImmediately,
          });
        } catch (socketError: unknown) {
          const error = socketError instanceof Error ? socketError : new Error(String(socketError));
          logger.error('Failed to send notification for Python API failures', {
            error: error.message,
            stack: error.stack,
            userId,
            kbId: finalKbId,
            folderId: finalFolderId,
          });
        }
      }
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
    
      // Send notification for all files as failed if there's a catastrophic error
      // Event-driven: Send immediately when catastrophic error occurs
    if (notificationService && placeholderResults.length > 0) {
      try {
        // Use provided kbId and folderId, or extract from URL as fallback
        const errorKbId = kbId || (() => {
          const urlParts = pythonServiceUrl.split('/');
          const kbIdIndex = urlParts.findIndex((part) => part === 'kb') + 1;
          return kbIdIndex > 0 && kbIdIndex < urlParts.length ? urlParts[kbIdIndex] : undefined;
        })();
        const errorFolderId = folderId || (() => {
          const urlParts = pythonServiceUrl.split('/');
          const folderIdIndex = urlParts.findIndex((part) => part === 'folder');
          return folderIdIndex > 0 && folderIdIndex + 1 < urlParts.length ? urlParts[folderIdIndex + 1] : undefined;
        })();

        const failedFiles = placeholderResults.map((result) => ({
          recordId: result.metadata.key,
          fileName: result.metadata.fileName,
          filePath: result.metadata.filePath,
          error: error.message || 'Processing failed',
        }));

        const eventData = {
          failedFiles,
          orgId,
          kbId: errorKbId,
          folderId: errorFolderId,
          totalFailed: placeholderResults.length,
          timestamp: Date.now(),
        };

        // Send immediately - service handles connection state and queuing
        const sentImmediately = notificationService.sendToUser(userId, 'records:failed', eventData);

        logger.info('Notification sent for catastrophic failure', {
          userId,
          kbId: errorKbId,
          folderId: errorFolderId,
          totalFailed: placeholderResults.length,
          sentImmediately,
        });
      } catch (socketError: unknown) {
        const error = socketError instanceof Error ? socketError : new Error(String(socketError));
        logger.error('Failed to send notification for catastrophic failure', {
          error: error.message,
          stack: error.stack,
          userId,
          kbId: kbId || undefined,
          folderId: folderId || undefined,
        });
      }
    }
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
