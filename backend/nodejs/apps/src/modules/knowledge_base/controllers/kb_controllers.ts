import { v4 as uuidv4 } from 'uuid';
import { AuthenticatedUserRequest } from './../../../libs/middlewares/types';
import { NextFunction, Response } from 'express';
import { Logger } from '../../../libs/services/logger.service';
import { RecordRelationService } from '../services/kb.relation.service';
import { IRecordDocument } from '../types/record';
import { IFileRecordDocument } from '../types/file_record';
import {
  BadRequestError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import {
  saveFileToStorageAndGetDocumentId,
  uploadNextVersionToStorage,
} from '../utils/utils';
import {
  INDEXING_STATUS,
  ORIGIN_TYPE,
  RECORD_TYPE,
  RELATIONSHIP_TYPE,
} from '../constants/record.constants';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { AIServiceResponse } from '../../enterprise_search/types/conversation.interfaces';
import {
  IServiceDeleteRecordResponse,
  IServiceRecordsResponse,
} from '../types/service.records.response';
import axios from 'axios';
import { ArangoService } from '../../../libs/services/arango.service';

const logger = Logger.getInstance({
  service: 'Knowledge Base Controller',
});
const AI_SERVICE_UNAVAILABLE_MESSAGE =
  'AI Service is currently unavailable. Please check your network connection or try again later.';

const CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE =
  'Connector Service is currently unavailable. Please check your network connection or try again later.';

export const createRecords =
  (
    recordRelationService: RecordRelationService,
    keyValueStoreService: KeyValueStoreService,
    appConfig: AppConfig,
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const files = req.body.fileBuffers;
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const { recordName } = req.body;
      const isVersioned = req.body?.isVersioned || true;

      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      const currentTime = Date.now();

      // First ensure the user exists in the database
      const userDoc = await recordRelationService.findOrCreateUser(
        userId,
        req.user?.email || '',
        orgId,
        req.user?.firstName,
        req.user?.lastName,
        req.user?.middleName,
        req.user?.designation,
      );

      // Get or create a knowledge base for this organization
      const kb = await recordRelationService.getOrCreateKnowledgeBase(
        userId,
        orgId,
      );

      // Make sure the user has permission on this knowledge base
      await recordRelationService.createKbUserPermission(
        kb._key,
        userDoc._key,
        RELATIONSHIP_TYPE.USER,
        'OWNER',
      );

      const records: IRecordDocument[] = [];
      const fileRecords: IFileRecordDocument[] = [];

      // Process files
      for (const file of files) {
        const { originalname, mimetype, size, lastModified } = file;
        const extension = originalname.includes('.')
          ? originalname
              .substring(originalname.lastIndexOf('.') + 1)
              .toLowerCase()
          : null;
        // Generate a unique ID for the record
        const key: string = uuidv4();

        const webUrl = `/record/${key}`;

        console.log('Create Records - File metadata:', {
          fileName: file.originalname,
          lastModified,
          lastModifiedType: typeof lastModified,
          fileKeys: Object.keys(file),
        });

        // Ensure lastModified is a valid Unix timestamp
        const validLastModified =
          lastModified && !isNaN(lastModified) && lastModified > 0
            ? lastModified
            : currentTime;

        console.log('Create Records - Timestamp validation:', {
          fileName: file.originalname,
          originalLastModified: lastModified,
          validLastModified,
          currentTime,
          usingFallback: validLastModified === currentTime,
        });

        const record = {
          _key: key,
          orgId: orgId,
          recordName: '',
          externalRecordId: '',
          recordType: RECORD_TYPE.FILE,
          origin: ORIGIN_TYPE.UPLOAD,
          createdAtTimestamp: currentTime,
          updatedAtTimestamp: currentTime,
          sourceCreatedAtTimestamp: validLastModified, // Use the file's last modified time
          sourceLastModifiedTimestamp: validLastModified, // Also store it as last modified
          isDeleted: false,
          isArchived: false,
          indexingStatus: INDEXING_STATUS.NOT_STARTED,
          version: 1,
          webUrl: webUrl,
        };

        const fileRecord = {
          _key: key,
          orgId: orgId,
          name: '',
          isFile: true,
          extension: extension,
          mimeType: mimetype,
          sizeInBytes: size,
          webUrl: webUrl,
          path: '/',
        };

        // Get document ID from storage
        const { documentId, documentName } =
          await saveFileToStorageAndGetDocumentId(
            req,
            file,
            originalname,
            isVersioned,
            record,
            fileRecord,
            keyValueStoreService,
            appConfig.storage,
            recordRelationService,
          );

        // Update record and fileRecord with the returned values
        record.recordName = recordName || documentName;
        record.externalRecordId = documentId;

        fileRecord.name = documentName;

        // Prepare file record object
        records.push(record);
        fileRecords.push(fileRecord);
      }

      // Use the service method to insert records and file records in a transaction
      let result;
      try {
        result = await recordRelationService.insertRecordsAndFileRecords(
          records,
          fileRecords,
          keyValueStoreService,
        );
        logger.info(
          `Successfully inserted ${result.insertedRecords.length} records and file records`,
        );
      } catch (insertError) {
        logger.error('Failed to insert records and file records', {
          error: insertError,
        });
        throw new InternalServerError(
          insertError instanceof Error
            ? insertError.message
            : 'Unexpected error occurred',
        );
      }

      // Create relationships in a separate try-catch block
      try {
        // Now create relationships between entities
        for (let i = 0; i < result.insertedRecords.length; i++) {
          const recordId = result.insertedRecords[i]?._key;
          const fileRecordId = result.insertedFileRecords[i]?._key;

          // Create is_of_type relationship between record and file record
          if (recordId && fileRecordId) {
            await recordRelationService.createRecordToFileRecordRelationship(
              recordId,
              fileRecordId,
            );
          }

          // Add record to the knowledge base
          if (recordId) {
            await recordRelationService.addRecordToKnowledgeBase(
              kb._key,
              recordId,
            );
          }
        }

        logger.info(
          `Created relationships for ${result.insertedRecords.length} records`,
        );

        // Send the response after all operations succeed
        res.status(201).json({
          message: 'Records created successfully',
          data: {
            recordCount: result.insertedRecords.length,
            knowledgeBase: {
              id: kb._key,
              name: kb.name,
            },
            records: result.insertedRecords.map((record) => ({
              id: record._key,
              name: record.recordName,
              type: record.recordType,
            })),
          },
        });
      } catch (relationError: any) {
        // Handle relationship creation errors separately
        logger.error('Error creating relationships', { error: relationError });

        // Pass the error to the next middleware
        next(relationError);
      }
    } catch (error: any) {
      logger.error('Error creating records', { error });
      next(error);
    }
  };

export const getRecordById =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { recordId } = req.params as { recordId: string };
      const userId = req.user?.userId;
      const aiBackendUrl = appConfig.aiBackend;
      if (!userId) {
        throw new BadRequestError('User not authenticated');
      }

      try {
        const aiCommand = new AIServiceCommand({
          uri: `${aiBackendUrl}/api/v1/records/${recordId}`,
          method: HttpMethod.GET,
          headers: req.headers as Record<string, string>,
          // body: { query, limit },
        });

        let aiResponse;
        try {
          aiResponse =
            (await aiCommand.execute()) as AIServiceResponse<IServiceRecordsResponse>;
        } catch (error: any) {
          if (error.cause && error.cause.code === 'ECONNREFUSED') {
            throw new InternalServerError(
              AI_SERVICE_UNAVAILABLE_MESSAGE,
              error,
            );
          }
          logger.error(' Failed error ', error);
          throw new InternalServerError('Failed to get AI response', error);
        }
        if (!aiResponse || aiResponse.statusCode !== 200 || !aiResponse.data) {
          throw new InternalServerError(
            'Failed to get response from AI service',
            aiResponse?.data,
          );
        }

        const recordData = aiResponse.data;

        res.status(200).json({
          ...recordData,
          meta: {
            requestId: req.context?.requestId,
            timestamp: new Date().toISOString(),
          },
        });
        return; // Added return statement
      } catch (error: any) {
        if (error.message?.includes('not found')) {
          throw new NotFoundError('Record not found');
        }

        if (error.message?.includes('does not have permission')) {
          throw new UnauthorizedError(
            'You do not have permission to access this record',
          );
        }

        throw error;
      }
    } catch (error: any) {
      logger.error('Error getting record by id', {
        recordId: req.params.recordId,
        error,
      });
      next(error);
      return; // Added return statement
    }
  };

export const getRecordBuffer =
  (connectorUrl: string) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { recordId } = req.params as { recordId: string };
      const { userId, orgId } = req.user || {};

      if (!userId || !orgId) {
        throw new BadRequestError('User authentication is required');
      }

      // Make request to FastAPI backend
      const response = await axios.get(
        `${connectorUrl}/api/v1/stream/record/${recordId}`,
        {
          responseType: 'stream',
          headers: {
            // Include any necessary headers, such as authentication
            Authorization: req.headers.authorization,
            'Content-Type': 'application/json',
          },
        },
      );

      // Set appropriate headers from the FastAPI response
      res.set('Content-Type', response.headers['content-type']);
      if (response.headers['content-disposition']) {
        res.set('Content-Disposition', response.headers['content-disposition']);
      }

      // Pipe the streaming response directly to the client
      response.data.pipe(res);

      // Handle any errors in the stream
      response.data.on('error', (error: any) => {
        console.error('Stream error:', error);
        // Only send error if headers haven't been sent yet
        if (!res.headersSent) {
          throw new InternalServerError('Error streaming data');
        }
      });
    } catch (error: any) {
      console.error('Error fetching record buffer:', error);
      if (!res.headersSent) {
        if (error.response) {
          // Forward status code and error from FastAPI
          res.status(error.response.status).json({
            error: error.response.data || 'Error from AI backend',
          });
        } else {
          throw new InternalServerError('Failed to retrieve record data');
        }
      }
      next(error);
    }
  };

/**
 * Update a record
 */

export const updateRecord =
  (
    recordRelationService: RecordRelationService,
    keyValueStoreService: KeyValueStoreService,
    defaultConfig: DefaultStorageConfig,
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { recordId } = req.params as { recordId: string };
      const { userId, orgId } = req.user || {};
      const updateData = req.body || {};

      if (!userId || !orgId) {
        throw new BadRequestError('User authentication is required');
      }

      // Check if there's a file in the request
      const hasFileBuffer = req.body.fileBuffer && req.body.fileBuffer.buffer;
      let originalname, mimetype, size, extension;

      if (hasFileBuffer) {
        ({ originalname, mimetype, size } = req.body.fileBuffer);

        // Extract extension from filename
        extension = originalname.includes('.')
          ? originalname
              .substring(originalname.lastIndexOf('.') + 1)
              .toLowerCase()
          : null;
      }

      // Only check for empty updateData if there are no files
      if (Object.keys(updateData).length === 0 && !hasFileBuffer) {
        throw new BadRequestError('No update data or files provided');
      }

      // Check if user has permission to update records
      try {
        await recordRelationService.validateUserKbAccess(userId, orgId, [
          'OWNER',
          'WRITER',
          'FILEORGANIZER',
        ]);
      } catch (error) {
        throw new ForbiddenError('Permission denied');
      }

      // Get the current record to determine what's changing
      let existingRecord;
      try {
        existingRecord = await recordRelationService.getRecordById(
          recordId,
          userId,
          orgId,
        );

        if (!existingRecord || !existingRecord.record) {
          throw new NotFoundError(`Record with ID ${recordId} not found`);
        }
      } catch (error) {
        throw new NotFoundError(`Record with ID ${recordId} not found`);
      }

      // Expanded list of immutable fields based on record schema
      const immutableFields = [
        '_id',
        '_key',
        '_rev',
        'orgId',
        'userId',
        'createdAtTimestamp',
        'externalRecordId', // Generally shouldn't change
        'recordType', // Type shouldn't change after creation
        'origin', // Origin shouldn't change after creation
      ];

      const attemptedImmutableUpdates = immutableFields.filter(
        (field) => updateData[field] !== undefined,
      );

      if (attemptedImmutableUpdates.length > 0) {
        throw new BadRequestError(
          `Cannot update immutable fields: ${attemptedImmutableUpdates.join(', ')}`,
        );
      }

      // Prepare update data with timestamp
      const updatedData = {
        ...updateData,
        updatedAtTimestamp: Date.now(),
        isLatestVersion: true,
      };

      // Add file-related data if file is being uploaded
      if (hasFileBuffer) {
        // Note: sizeInBytes is NOT part of the record schema, only file record schema
        // Pass file metadata for service validation and file record update
        updatedData.fileMetadata = {
          originalname,
          mimetype,
          size,
          extension,
        };
      }

      // Prepare file name and record name if file upload
      let fileName = '';
      if (hasFileBuffer) {
        fileName = originalname;
        // Get filename without extension to use as record name
        if (fileName && fileName.includes('.')) {
          const lastDotIndex = fileName.lastIndexOf('.');
          if (lastDotIndex > 0) {
            // Ensure there's a name part before the extension
            updatedData.recordName = fileName.substring(0, lastDotIndex);
            logger.info('Setting record name from file', {
              recordName: updatedData.recordName,
              originalFileName: fileName,
            });
          }
        }

        // Prepare version increment
        updatedData.version = (existingRecord.record.version || 0) + 1;
      }

      // Handle soft delete case
      if (updatedData.isDeleted === true && !existingRecord.record.isDeleted) {
        updatedData.deletedByUserId = userId;
        updatedData.deletedAtTimestamp = Date.now();

        // If this is a file, mark it as no longer latest version
        if (existingRecord.record.recordType === 'FILE') {
          updatedData.isLatestVersion = false;
        }

        logger.info('Soft-deleting record', { recordId, userId });
      }

      // STEP 1: Update the record in the database FIRST (before storage)
      logger.info('Updating record in database', {
        recordId,
        hasFileUpload: hasFileBuffer,
        updatedFields: Object.keys(updatedData).filter(
          (key) => key !== 'fileMetadata',
        ),
      });

      const updatedRecord = await recordRelationService.updateRecord(
        recordId,
        updatedData,
        keyValueStoreService,
      );

      // STEP 2: Upload file to storage ONLY after database update succeeds
      let fileUploaded = false;
      let storageDocumentId = null;

      if (hasFileBuffer) {
        // Use the externalRecordId as the storageDocumentId
        storageDocumentId = existingRecord.record.externalRecordId;

        // Check if we have a valid externalRecordId to use
        if (!storageDocumentId) {
          // If database update succeeded but no external ID, we need to rollback
          logger.error('No external record ID found after database update', {
            recordId,
            updatedRecord: updatedRecord._key,
          });
          throw new BadRequestError(
            'Cannot update file: No external record ID found for this record',
          );
        }

        // Log the file upload attempt
        logger.info('Uploading new version of file to storage', {
          recordId,
          fileName: originalname,
          fileSize: size,
          mimeType: mimetype,
          extension,
          storageDocumentId: storageDocumentId,
          version: updatedData.version,
        });

        try {
          // Update version through storage service using externalRecordId
          const fileBuffer = req.body.fileBuffer;
          await uploadNextVersionToStorage(
            req,
            fileBuffer,
            storageDocumentId,
            keyValueStoreService,
            defaultConfig,
          );

          logger.info('File uploaded to storage successfully', {
            recordId,
            storageDocumentId,
            version: updatedData.version,
          });

          fileUploaded = true;
        } catch (storageError: any) {
          logger.error(
            'Failed to upload file to storage after database update',
            {
              recordId,
              storageDocumentId: storageDocumentId,
              error: storageError.message,
              version: updatedData.version,
            },
          );

          // TODO: Consider implementing rollback mechanism here
          // For now, we log the inconsistent state but don't fail the request
          // since the database update was successful
          logger.warn(
            'Database updated but storage upload failed - inconsistent state',
            {
              recordId,
              storageDocumentId,
              databaseVersion: updatedRecord.version,
            },
          );

          throw new InternalServerError(
            `Record updated but file upload failed: ${storageError.message}. Please retry the file upload.`,
          );
        }
      }

      // Log the successful update
      logger.info('Record updated successfully', {
        recordId,
        userId,
        orgId,
        fileUploaded,
        newFileName: fileUploaded ? fileName : undefined,
        updatedFields: Object.keys(updatedData).filter(
          (key) => key !== 'fileMetadata',
        ),
        version: updatedRecord.version,
        requestId: req.context?.requestId,
      });

      // Return the updated record
      res.status(200).json({
        message: fileUploaded
          ? 'Record updated with new file version'
          : 'Record updated successfully',
        record: updatedRecord,
        meta: {
          requestId: req.context?.requestId,
          timestamp: new Date().toISOString(),
        },
      });
    } catch (error: any) {
      // Log the error for debugging
      logger.error('Error updating record', {
        recordId: req.params.recordId,
        error: error.message,
        stack: error.stack,
        userId: req.user?.userId,
        orgId: req.user?.orgId,
        requestId: req.context?.requestId,
      });

      next(error);
    }
  };

/**
 * Delete (soft-delete) a record
 */

/**
 * Interface for record permission from AI service
 */
interface RecordPermission {
  id: string;
  name: string;
  type: string;
  relationship: string;
}

/**
 * Controller function for deleting records via AI service
 * Handles both KB-type records and direct access records
 */
// Separate utility function for performing hard delete
export const performHardDelete = async (
  recordId: string,
  headers: Record<string, string>,
  connectorBackendUrl: string,
  requestId?: string,
): Promise<boolean> => {
  logger.info('Performing hard delete operation', {
    recordId,
    requestId,
  });

  try {
    const hardDeleteCommand = new AIServiceCommand({
      uri: `${connectorBackendUrl}/api/v1/delete/record/${recordId}`,
      method: HttpMethod.DELETE,
      headers: headers,
    });

    const hardDeleteResponse =
      (await hardDeleteCommand.execute()) as AIServiceResponse<IServiceDeleteRecordResponse>;

    if (!hardDeleteResponse || hardDeleteResponse.statusCode !== 200) {
      logger.error('Failed to hard delete record', {
        recordId,
        statusCode: hardDeleteResponse?.statusCode,
        requestId,
      });

      throw new InternalServerError(
        'Failed to hard delete record',
        hardDeleteResponse?.data,
      );
    }

    logger.info('Record hard-deleted successfully', {
      recordId,
      requestId,
    });

    return true;
  } catch (error: any) {
    logger.error('Error in hard delete operation', {
      recordId,
      error: error.message,
      requestId,
    });

    if (error.cause && error.cause.code === 'ECONNREFUSED') {
      throw new InternalServerError(
        CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    if (error.message?.includes('not found')) {
      throw new NotFoundError('Record not found');
    }

    if (error.message?.includes('User has no access to this record')) {
      throw new UnauthorizedError('User has no access to this record');
    }

    throw error;
  }
};

/**
 * Controller function for deleting records via AI service
 * Handles both KB-type records and direct access records
 */
export const deleteRecord =
  (recordRelationService: RecordRelationService, appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { recordId } = req.params as { recordId: string };
      const { userId, orgId } = req.user || {};
      const queryBackendUrl = appConfig.aiBackend;
      const connectorBackendUrl = appConfig.connectorBackend;

      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication is required');
      }

      logger.info('Processing record deletion request', {
        recordId,
        userId,
        orgId,
        requestId: req.context?.requestId,
      });

      // First get the record via AI service to verify existence and access
      let existingRecord;
      try {
        // Set up AI service command to fetch record details
        const aiCommand = new AIServiceCommand({
          uri: `${queryBackendUrl}/api/v1/records/${recordId}`,
          method: HttpMethod.GET,
          headers: req.headers as Record<string, string>,
        });

        let aiResponse;
        try {
          aiResponse =
            (await aiCommand.execute()) as AIServiceResponse<IServiceRecordsResponse>;
        } catch (error: any) {
          if (error.cause && error.cause.code === 'ECONNREFUSED') {
            throw new InternalServerError(
              AI_SERVICE_UNAVAILABLE_MESSAGE,
              error,
            );
          }
          throw new InternalServerError('Failed to get AI response', error);
        }

        if (!aiResponse || aiResponse.statusCode !== 200 || !aiResponse.data) {
          throw new InternalServerError(
            'Failed to get response from AI service',
            aiResponse?.data,
          );
        }

        existingRecord = aiResponse.data;

        // Verify the record exists and has required data
        if (!existingRecord || !existingRecord.record) {
          logger.warn('Record not found or invalid response from AI service', {
            recordId,
            userId,
            orgId,
            requestId: req.context?.requestId,
          });
          throw new NotFoundError(`Record with ID ${recordId} not found`);
        }

        // Check permissions based on source type
        const isConnectorRecord = existingRecord.record.origin === 'CONNECTOR';

        // For KB records, verify KB access
        if (!isConnectorRecord) {
          try {
            // Verify user has delete permission at KB level
            await recordRelationService.validateUserKbAccess(userId, orgId, [
              'OWNER',
              'WRITER',
              'FILEORGANIZER',
            ]);
          } catch (error) {
            logger.warn('User lacks KB permissions for record deletion', {
              userId,
              orgId,
              recordId,
              error,
              requestId: req.context?.requestId,
            });
            throw new ForbiddenError('Permission denied for record deletion');
          }

          // For non-connector records, use hard delete
          logger.info('Non-connector record detected, using hard delete', {
            recordId,
            userId,
            requestId: req.context?.requestId,
          });

          let hardDeleteSuccessful = false;
          try {
            // Perform hard delete
            hardDeleteSuccessful = await performHardDelete(
              recordId,
              req.headers as Record<string, string>,
              connectorBackendUrl,
              req.context?.requestId,
            );
          } catch (hardDeleteError: any) {
            logger.error(
              'Hard delete operation failed, falling back to soft delete',
              {
                recordId,
                error: hardDeleteError.message,
                requestId: req.context?.requestId,
              },
            );
            logger.error('Failed to hard-deleted record', {
              recordId,
              error: hardDeleteError,
              requestId: req.context?.requestId,
            });
            throw hardDeleteError;
          }

          // If hard delete was successful, publish the event and send response
          if (hardDeleteSuccessful) {
            try {
              await recordRelationService.publishHardDeleteRecord(
                existingRecord,
              );
            } catch (eventError) {
              logger.error(
                'Failed to publish delete event for hard-deleted record',
                {
                  recordId,
                  error: eventError,
                  requestId: req.context?.requestId,
                },
              );
              // Continue despite event publishing error
            }

            // Return success response for hard delete
            res.status(200).json({
              message: 'Record permanently deleted successfully',
              meta: {
                requestId: req.context?.requestId,
                timestamp: new Date().toISOString(),
              },
            });

            return; // Return early to avoid soft delete logic
          }
        }
        // For connector records, check direct permissions
        else {
          // Verify permissions array exists
          if (
            !existingRecord.permissions ||
            !Array.isArray(existingRecord.permissions)
          ) {
            logger.warn('Record is missing permissions data', {
              recordId,
              userId,
              requestId: req.context?.requestId,
            });
            throw new ForbiddenError(
              'Cannot verify permissions for this record',
            );
          }

          // Check if any permission has the required relationship for deletion
          const hasDeletePermission = existingRecord.permissions.some(
            (permission: RecordPermission) =>
              ['OWNER', 'WRITER', 'FILEORGANIZER', 'READER'].includes(
                permission.relationship,
              ),
          );

          if (!hasDeletePermission) {
            logger.warn('User lacks direct permissions for record deletion', {
              userId,
              recordId,
              permissions: existingRecord.permissions,
              requestId: req.context?.requestId,
            });
            throw new ForbiddenError(
              'You do not have permission to delete this record',
            );
          }
        }

        logger.info('User has permission to delete record', {
          recordId,
          userId,
          isConnectorRecord,
          permissions: existingRecord.permissions,
          requestId: req.context?.requestId,
        });
      } catch (error) {
        if (
          error instanceof NotFoundError ||
          error instanceof ForbiddenError ||
          error instanceof UnauthorizedError ||
          error instanceof InternalServerError
        ) {
          throw error;
        }

        logger.error('Error fetching record details for deletion', {
          recordId,
          userId,
          orgId,
          error,
          requestId: req.context?.requestId,
        });
        throw new NotFoundError(
          `Record with ID ${recordId} not found or inaccessible`,
        );
      }

      const internalRecordId = existingRecord.record._key;

      // Perform the soft delete operation for connector records
      try {
        await recordRelationService.softDeleteRecord(internalRecordId, userId);

        logger.info('Record soft-deleted successfully', {
          recordId,
          internalRecordId,
          userId,
          orgId,
          requestId: req.context?.requestId,
        });

        res.status(200).json({
          message: 'Record deleted successfully',
          meta: {
            requestId: req.context?.requestId,
            timestamp: new Date().toISOString(),
          },
        });
      } catch (softDeleteError) {
        logger.error('Soft delete operation failed', {
          recordId,
          internalRecordId,
          error: softDeleteError,
          requestId: req.context?.requestId,
        });
        throw softDeleteError;
      }
    } catch (error: any) {
      // Log the error for debugging
      logger.error('Error in record deletion process', {
        recordId: req.params.recordId,
        error: error.message,
        stack: error.stack,
        userId: req.user?.userId,
        orgId: req.user?.orgId,
        requestId: req.context?.requestId,
      });

      next(error);
    }
  };

export const getRecords =
  (recordRelationService: RecordRelationService) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      // Extract user from request
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;

      // Validate user authentication
      if (!userId || !orgId) {
        throw new NotFoundError(
          'User not authenticated or missing organization ID',
        );
      }

      const { exists: userExists } =
        await recordRelationService.checkUserExists(userId, orgId);
      if (!userExists) {
        logger.warn('Attempting to fetch records for non-existent user', {
          userId,
          orgId,
          requestId: req.context?.requestId,
        });

        res.status(200).json({
          records: [],
          pagination: {
            page: 1,
            limit: 20,
            totalCount: 0,
            totalPages: 0,
          },
          meta: {
            requestId: req.context?.requestId,
            timestamp: new Date().toISOString(),
            message: 'User not found in system',
          },
        });
        return;
      }

      // Check if knowledge base exists
      const { exists: kbExists } = await recordRelationService.checkKBExists(
        userId,
        orgId,
      );
      if (!kbExists) {
        logger.warn(
          'Attempting to fetch records for organization without knowledge base',
          {
            userId,
            orgId,
            requestId: req.context?.requestId,
          },
        );

        res.status(200).json({
          records: [],
          pagination: {
            page: 1,
            limit: 20,
            totalCount: 0,
            totalPages: 0,
          },
          meta: {
            requestId: req.context?.requestId,
            timestamp: new Date().toISOString(),
            message: 'No knowledge base found for this organization',
          },
        });
        return;
      }

      // Extract and parse query parameters
      const page = req.query.page ? parseInt(String(req.query.page), 10) : 1;
      const limit = req.query.limit
        ? parseInt(String(req.query.limit), 10)
        : 20;
      const search = req.query.search ? String(req.query.search) : undefined;
      const recordTypes = req.query.recordTypes
        ? String(req.query.recordTypes).split(',')
        : undefined;
      const origins = req.query.origins
        ? String(req.query.origins).split(',')
        : undefined;

      // Add missing parameters
      const connectors = req.query.connectors
        ? String(req.query.connectors).split(',')
        : undefined;

      const permissions = req.query.permissions
        ? String(req.query.permissions).split(',')
        : undefined;

      const indexingStatus = req.query.indexingStatus
        ? String(req.query.indexingStatus).split(',')
        : undefined;

      // Parse date filters
      const dateFrom = req.query.dateFrom
        ? parseInt(String(req.query.dateFrom), 10)
        : undefined;
      const dateTo = req.query.dateTo
        ? parseInt(String(req.query.dateTo), 10)
        : undefined;

      // Sorting parameters
      const sortBy = req.query.sortBy ? String(req.query.sortBy) : undefined;
      const sortOrderParam = req.query.sortOrder
        ? String(req.query.sortOrder)
        : undefined;
      const sortOrder =
        sortOrderParam === 'asc' || sortOrderParam === 'desc'
          ? sortOrderParam
          : undefined;

      // Parse source parameter
      const source = req.query.source
        ? ['all', 'local', 'connector'].includes(String(req.query.source))
          ? (String(req.query.source) as 'all' | 'local' | 'connector')
          : 'all'
        : 'all';

      // Debug log for troubleshooting filter parameters
      logger.debug('API Controller parameters', {
        userId,
        orgId,
        source,
        connectors,
        requestId: req.context?.requestId,
      });

      // Retrieve records using the service
      const result = await recordRelationService.getRecords({
        orgId,
        userId,
        page,
        limit,
        search,
        recordTypes,
        origins,
        connectors,
        permissions,
        indexingStatus,
        dateFrom,
        dateTo,
        sortBy,
        sortOrder,
        source,
      });

      // Log successful retrieval
      logger.info('Records retrieved successfully', {
        totalRecords: result.pagination.totalCount,
        page: result.pagination.page,
        requestId: req.context?.requestId,
      });

      // Send response
      res.status(200).json({
        ...result,
        meta: {
          requestId: req.context?.requestId,
          timestamp: new Date().toISOString(),
        },
      });
    } catch (error) {
      // Handle permission errors
      if (
        error instanceof Error &&
        (error.message.includes('does not have permission') ||
          error.message.includes('does not have the required permissions'))
      ) {
        throw new UnauthorizedError(
          'You do not have permission to access these records',
        );
      }

      // Log and forward any other errors
      logger.error('Error getting records', {
        error: error instanceof Error ? error.message : 'Unknown error',
        stack: error instanceof Error ? error.stack : undefined,
        requestId: req.context?.requestId,
      });
      next(error);
    }
  };

/**
 * Archive a record
 */
export const archiveRecord =
  (
    recordRelationService: RecordRelationService,
    keyValueStoreService: KeyValueStoreService,
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { recordId } = req.params as { recordId: string };
      const { userId, orgId } = req.user || {};

      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication is required');
      }

      // Check if user has permission to archive records
      try {
        await recordRelationService.validateUserKbAccess(userId, orgId, [
          'OWNER',
          'WRITER',
          'FILEORGANIZER',
        ]);
      } catch (error) {
        throw new ForbiddenError('Permission denied');
      }

      // Get the current record to confirm it exists
      let existingRecord;
      try {
        existingRecord = await recordRelationService.getRecordById(
          recordId,
          userId,
          orgId,
        );
        if (!existingRecord || !existingRecord.record) {
          throw new NotFoundError(`Record with ID ${recordId} not found`);
        }
      } catch (error) {
        throw new NotFoundError(`Record with ID ${recordId} not found`);
      }

      // Check if record is already archived
      if (existingRecord.record.isArchived) {
        throw new ForbiddenError(
          `Record with ID ${recordId} is already archived`,
        );
      }

      // Prepare update data for archiving
      const archiveData = {
        isArchived: true,
        archivedBy: userId,
        archivedAtTimestamp: Date.now(),
        updatedAtTimestamp: Date.now(),
        isFileRecordUpdate: existingRecord.record.fileRecord ? true : false,
      };

      // Update the record in the database
      const archivedRecord = await recordRelationService.updateRecord(
        recordId,
        archiveData,
        keyValueStoreService,
      );

      // Log the successful archive
      logger.info('Record archived successfully', {
        recordId,
        userId,
        orgId,
        requestId: req.context?.requestId,
      });

      // Return the archived record
      res.status(200).json({
        message: 'Record archived successfully',
        record: {
          id: archivedRecord._key,
          name: archivedRecord.recordName,
          isArchived: archivedRecord.isArchived,
          archivedAt: new Date(archivedRecord.archivedAtTimestamp),
          archivedBy: archivedRecord.archivedBy,
        },
        meta: {
          requestId: req.context?.requestId,
          timestamp: new Date().toISOString(),
        },
      });
    } catch (error: any) {
      // Log the error for debugging
      logger.error('Error archiving record', {
        recordId: req.params.recordId,
        error: error.message,
        stack: error.stack,
        userId: req.user?.userId,
        orgId: req.user?.orgId,
        requestId: req.context?.requestId,
      });

      next(error);
    }
  };

/**
 * Unarchive a record
 */
export const unarchiveRecord =
  (
    recordRelationService: RecordRelationService,
    keyValueStoreService: KeyValueStoreService,
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { recordId } = req.params as { recordId: string };
      const { userId, orgId } = req.user || {};

      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication is required');
      }

      // Check if user has permission to unarchive records
      try {
        await recordRelationService.validateUserKbAccess(userId, orgId, [
          'OWNER',
          'WRITER',
          'FILEORGANIZER',
        ]);
      } catch (error) {
        res.status(403).json({
          message: error instanceof Error ? error.message : 'Permission denied',
          error: 'FORBIDDEN',
        });
        return;
      }

      // Get the current record to confirm it exists
      let existingRecord;
      try {
        existingRecord = await recordRelationService.getRecordById(
          recordId,
          userId,
          orgId,
        );
        if (!existingRecord || !existingRecord.record) {
          throw new NotFoundError(`Record with ID ${recordId} not found`);
        }
      } catch (error) {
        throw new NotFoundError(`Record with ID ${recordId} not found`);
      }

      // Check if record is already unarchived
      if (!existingRecord.record.isArchived) {
        throw new ForbiddenError(`Record with ID ${recordId} is not archived`);
      }

      // Prepare update data for unarchiving
      const unarchiveData = {
        isArchived: false,
        // We keep the archivedBy and archivedAtTimestamp for historical purposes
        // But we add the unarchive information
        unarchivedBy: userId,
        unarchivedAtTimestamp: Date.now(),
        updatedAtTimestamp: Date.now(),
        isFileRecordUpdate: existingRecord.record.fileRecord ? true : false,
      };

      // Update the record in the database
      const unarchivedRecord = await recordRelationService.updateRecord(
        recordId,
        unarchiveData,
        keyValueStoreService,
      );

      // Log the successful unarchive
      logger.info('Record unarchived successfully', {
        recordId,
        userId,
        orgId,
        requestId: req.context?.requestId,
      });

      // Return the unarchived record
      res.status(200).json({
        message: 'Record unarchived successfully',
        record: {
          id: unarchivedRecord._key,
          name: unarchivedRecord.recordName,
          isArchived: unarchivedRecord.isArchived,
          unarchivedAt: new Date(unarchivedRecord.unarchivedAtTimestamp),
          unarchivedBy: unarchivedRecord.unarchivedBy,
        },
        meta: {
          requestId: req.context?.requestId,
          timestamp: new Date().toISOString(),
        },
      });
    } catch (error: any) {
      // Log the error for debugging
      logger.error('Error unarchiving record', {
        recordId: req.params.recordId,
        error: error.message,
        stack: error.stack,
        userId: req.user?.userId,
        orgId: req.user?.orgId,
        requestId: req.context?.requestId,
      });

      next(error);
    }
  };

export const reindexRecord =
  (
    recordRelationService: RecordRelationService,
    keyValueStoreService: KeyValueStoreService,
    appConfig: AppConfig,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { recordId } = req.params as { recordId: string };
      const userId = req.user?.userId;
      const aiBackendUrl = appConfig.aiBackend;
      if (!userId) {
        throw new BadRequestError('User not authenticated');
      }

      try {
        const aiCommand = new AIServiceCommand({
          uri: `${aiBackendUrl}/api/v1/records/${recordId}`,
          method: HttpMethod.GET,
          headers: req.headers as Record<string, string>,
          // body: { query, limit },
        });

        let aiResponse;
        try {
          aiResponse =
            (await aiCommand.execute()) as AIServiceResponse<IServiceRecordsResponse>;
        } catch (error: any) {
          if (error.cause && error.cause.code === 'ECONNREFUSED') {
            throw new InternalServerError(
              AI_SERVICE_UNAVAILABLE_MESSAGE,
              error,
            );
          }
          logger.error(' Failed error ', error);
          throw new InternalServerError('Failed to get AI response', error);
        }
        if (!aiResponse || aiResponse.statusCode !== 200 || !aiResponse.data) {
          throw new UnauthorizedError(
            'User has no access to this record',
            aiResponse?.data,
          );
        }

        const recordData = aiResponse.data;
        const record = recordData.record;

        const reindexResponse = await recordRelationService.reindexRecord(
          recordId,
          record,
          keyValueStoreService,
        );

        res.status(200).json({
          reindexResponse,
        });

        return; // Added return statement
      } catch (error: any) {
        if (error.message?.includes('not found')) {
          throw new NotFoundError('Record not found');
        }

        if (error.message?.includes('User has no access to this record')) {
          throw new UnauthorizedError('User has no access to this record');
        }

        throw error;
      }
    } catch (error: any) {
      logger.error('Error getting record by id', {
        recordId: req.params.recordId,
        error,
      });
      next(error);
      return; // Added return statement
    }
  };

/**
 * Retrieves complete statistics for all connectors from ArangoDB
 * @param {ArangoService} arangoService - The ArangoDB service instance
 * @returns {Function} Express middleware function
 */
export const getConnectorStats =
  (arangoService: ArangoService) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const orgId = req.user?.orgId;

      if (!orgId) {
        res.status(400).json({
          success: false,
          message: 'Organization ID is required',
        });
        return;
      }

      // Get database connection
      const db = arangoService.getConnection();

      // Base filter for organization
      const baseFilter = `doc.orgId == "${orgId}" AND doc.recordType!="DRIVE"`;

      // AQL query with enhanced connector statistics
      const query = `
        // Overall stats (across all records)
        LET total_stats = (
          FOR doc IN records
            FILTER ${baseFilter}
            COLLECT AGGREGATE
              total        = COUNT(1),
              not_started  = SUM(doc.indexingStatus == "NOT_STARTED" ? 1 : 0),
              in_progress  = SUM(doc.indexingStatus == "IN_PROGRESS" ? 1 : 0),
              completed    = SUM(doc.indexingStatus == "COMPLETED" ? 1 : 0),
              failed       = SUM(doc.indexingStatus == "FAILED" ? 1 : 0),
              not_supported= SUM(doc.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" ? 1 : 0),
              auto_index_off= SUM(doc.indexingStatus == "AUTO_INDEX_OFF" ? 1 : 0)
            RETURN {
              total,
              indexing_status: {
                NOT_STARTED: not_started,
                IN_PROGRESS: in_progress,
                COMPLETED: completed,
                FAILED: failed,
                FILE_TYPE_NOT_SUPPORTED: not_supported,
                AUTO_INDEX_OFF: auto_index_off
              }
            }
        )[0]
        
        // Overall stats for connectors only
        LET overall_connector_stats = (
          FOR doc IN records
            FILTER ${baseFilter} AND doc.origin == "CONNECTOR"
            COLLECT AGGREGATE
              total        = COUNT(1),
              not_started  = SUM(doc.indexingStatus == "NOT_STARTED" ? 1 : 0),
              in_progress  = SUM(doc.indexingStatus == "IN_PROGRESS" ? 1 : 0),
              completed    = SUM(doc.indexingStatus == "COMPLETED" ? 1 : 0),
              failed       = SUM(doc.indexingStatus == "FAILED" ? 1 : 0),
              not_supported= SUM(doc.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" ? 1 : 0),
              auto_index_off= SUM(doc.indexingStatus == "AUTO_INDEX_OFF" ? 1 : 0)
            RETURN {
              total,
              indexing_status: {
                NOT_STARTED: not_started,
                IN_PROGRESS: in_progress,
                COMPLETED: completed,
                FAILED: failed,
                FILE_TYPE_NOT_SUPPORTED: not_supported,
                AUTO_INDEX_OFF: auto_index_off
              }
            }
        )[0]

        // Upload stats
        LET upload_stats = (
          FOR doc IN records
            FILTER ${baseFilter} AND doc.origin == "UPLOAD"
            COLLECT AGGREGATE
              total        = COUNT(1),
              not_started  = SUM(doc.indexingStatus == "NOT_STARTED" ? 1 : 0),
              in_progress  = SUM(doc.indexingStatus == "IN_PROGRESS" ? 1 : 0),
              completed    = SUM(doc.indexingStatus == "COMPLETED" ? 1 : 0),
              failed       = SUM(doc.indexingStatus == "FAILED" ? 1 : 0),
              not_supported= SUM(doc.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" ? 1 : 0),
              auto_index_off= SUM(doc.indexingStatus == "AUTO_INDEX_OFF" ? 1 : 0)
            RETURN {
              total,
              indexing_status: {
                NOT_STARTED: not_started,
                IN_PROGRESS: in_progress,
                COMPLETED: completed,
                FAILED: failed,
                FILE_TYPE_NOT_SUPPORTED: not_supported,
                AUTO_INDEX_OFF: auto_index_off
              }
            }
        )[0]

        // Enhanced connector stats with record type breakdowns
        LET connector_data = (
          FOR doc IN records
            FILTER ${baseFilter} AND doc.origin == "CONNECTOR"
            COLLECT connector = doc.connectorName INTO groupDocs = doc
            
            // Calculate counts directly for better consistency
            LET statusCounts = {
              "total": LENGTH(groupDocs),
              "NOT_STARTED": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "NOT_STARTED" RETURN 1),
              "IN_PROGRESS": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "IN_PROGRESS" RETURN 1),
              "COMPLETED": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "COMPLETED" RETURN 1),
              "FAILED": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "FAILED" RETURN 1),
              "FILE_TYPE_NOT_SUPPORTED": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" RETURN 1),
              "AUTO_INDEX_OFF": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "AUTO_INDEX_OFF" RETURN 1)
            }
            
            // Record type breakdown for this connector
            LET record_types = (
              FOR d IN groupDocs
                COLLECT record_type = d.recordType INTO typeGroupDocs = d
                
                LET typeStatusCounts = {
                  "total": LENGTH(typeGroupDocs),
                  "NOT_STARTED": LENGTH(FOR td IN typeGroupDocs FILTER td.indexingStatus == "NOT_STARTED" RETURN 1),
                  "IN_PROGRESS": LENGTH(FOR td IN typeGroupDocs FILTER td.indexingStatus == "IN_PROGRESS" RETURN 1),
                  "COMPLETED": LENGTH(FOR td IN typeGroupDocs FILTER td.indexingStatus == "COMPLETED" RETURN 1),
                  "FAILED": LENGTH(FOR td IN typeGroupDocs FILTER td.indexingStatus == "FAILED" RETURN 1),
                  "FILE_TYPE_NOT_SUPPORTED": LENGTH(FOR td IN typeGroupDocs FILTER td.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" RETURN 1),
                  "AUTO_INDEX_OFF": LENGTH(FOR td IN typeGroupDocs FILTER td.indexingStatus == "AUTO_INDEX_OFF" RETURN 1)
                }
                
                RETURN {
                  record_type,
                  total: typeStatusCounts.total,
                  indexing_status: {
                    NOT_STARTED: typeStatusCounts.NOT_STARTED,
                    IN_PROGRESS: typeStatusCounts.IN_PROGRESS,
                    COMPLETED: typeStatusCounts.COMPLETED,
                    FAILED: typeStatusCounts.FAILED,
                    FILE_TYPE_NOT_SUPPORTED: typeStatusCounts.FILE_TYPE_NOT_SUPPORTED,
                    AUTO_INDEX_OFF: typeStatusCounts.AUTO_INDEX_OFF
                  }
                }
            )
            
            RETURN {
              connector,
              total: statusCounts.total,
              indexing_status: {
                NOT_STARTED: statusCounts.NOT_STARTED,
                IN_PROGRESS: statusCounts.IN_PROGRESS,
                COMPLETED: statusCounts.COMPLETED,
                FAILED: statusCounts.FAILED,
                FILE_TYPE_NOT_SUPPORTED: statusCounts.FILE_TYPE_NOT_SUPPORTED,
                AUTO_INDEX_OFF: statusCounts.AUTO_INDEX_OFF
              },
              by_record_type: record_types
            }
        )

        // Stats by record type across all connectors
        LET record_type_stats = (
          FOR doc IN records
            FILTER ${baseFilter} AND doc.origin == "CONNECTOR"
            COLLECT record_type = doc.recordType INTO groupDocs = doc
            
            // Calculate counts directly for better consistency
            LET statusCounts = {
              "total": LENGTH(groupDocs),
              "NOT_STARTED": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "NOT_STARTED" RETURN 1),
              "IN_PROGRESS": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "IN_PROGRESS" RETURN 1),
              "COMPLETED": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "COMPLETED" RETURN 1),
              "FAILED": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "FAILED" RETURN 1),
              "FILE_TYPE_NOT_SUPPORTED": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" RETURN 1),
              "AUTO_INDEX_OFF": LENGTH(FOR d IN groupDocs FILTER d.indexingStatus == "AUTO_INDEX_OFF" RETURN 1)
            }
            
            // Connector breakdown for this record type
            LET connectors_for_type = (
              FOR d IN groupDocs
                COLLECT connector = d.connectorName INTO connectorGroupDocs = d
                
                LET connectorStatusCounts = {
                  "total": LENGTH(connectorGroupDocs),
                  "NOT_STARTED": LENGTH(FOR cd IN connectorGroupDocs FILTER cd.indexingStatus == "NOT_STARTED" RETURN 1),
                  "IN_PROGRESS": LENGTH(FOR cd IN connectorGroupDocs FILTER cd.indexingStatus == "IN_PROGRESS" RETURN 1),
                  "COMPLETED": LENGTH(FOR cd IN connectorGroupDocs FILTER cd.indexingStatus == "COMPLETED" RETURN 1),
                  "FAILED": LENGTH(FOR cd IN connectorGroupDocs FILTER cd.indexingStatus == "FAILED" RETURN 1),
                  "FILE_TYPE_NOT_SUPPORTED": LENGTH(FOR cd IN connectorGroupDocs FILTER cd.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" RETURN 1),
                  "AUTO_INDEX_OFF": LENGTH(FOR cd IN connectorGroupDocs FILTER cd.indexingStatus == "AUTO_INDEX_OFF" RETURN 1)
                }
                
                RETURN {
                  connector,
                  total: connectorStatusCounts.total,
                  indexing_status: {
                    NOT_STARTED: connectorStatusCounts.NOT_STARTED,
                    IN_PROGRESS: connectorStatusCounts.IN_PROGRESS,
                    COMPLETED: connectorStatusCounts.COMPLETED,
                    FAILED: connectorStatusCounts.FAILED,
                    FILE_TYPE_NOT_SUPPORTED: connectorStatusCounts.FILE_TYPE_NOT_SUPPORTED,
                    AUTO_INDEX_OFF: connectorStatusCounts.AUTO_INDEX_OFF
                  }
                }
            )
            
            RETURN {
              record_type,
              total: statusCounts.total,
              indexing_status: {
                NOT_STARTED: statusCounts.NOT_STARTED,
                IN_PROGRESS: statusCounts.IN_PROGRESS,
                COMPLETED: statusCounts.COMPLETED,
                FAILED: statusCounts.FAILED,
                FILE_TYPE_NOT_SUPPORTED: statusCounts.FILE_TYPE_NOT_SUPPORTED,
                AUTO_INDEX_OFF: statusCounts.AUTO_INDEX_OFF
              },
              by_connector: connectors_for_type
            }
        )

        // Return all stats
        RETURN {
          org_id: "${orgId}",
          total: total_stats,
          overall_connector: overall_connector_stats,
          upload: upload_stats,
          by_connector: connector_data,
          by_record_type: record_type_stats
        }
      `;

      // Execute the query
      const cursor = await db.query(query);
      const result = await cursor.all();

      // Return the first item if it's an array
      const data =
        Array.isArray(result) && result.length === 1 ? result[0] : result;

      // Add validation to verify consistency
      const validateStatConsistency = (data: any) => {
        // Check connector data
        for (const connector of data.by_connector) {
          // Calculate sum of status counts to compare with total
          const statusSum = Object.values(connector.indexing_status).reduce(
            (sum: number, count) => sum + Number(count),
            0,
          );

          // Verify connector total matches sum of status counts
          if (statusSum !== connector.total) {
            logger.warn(
              `Connector ${connector.connector}: Total (${connector.total}) doesn't match sum of status counts (${statusSum})`,
            );
          }

          // Verify record type stats within this connector
          let recordTypeTotal = 0;
          for (const recordType of connector.by_record_type) {
            // Sum status counts within this record type
            const recordTypeStatusSum = Object.values(
              recordType.indexing_status,
            ).reduce((sum: number, count) => sum + Number(count), 0);

            // Verify record type total matches sum of its status counts
            if (recordTypeStatusSum !== recordType.total) {
              logger.warn(
                `Connector ${connector.connector}, Record type ${recordType.record_type}: ` +
                  `Total (${recordType.total}) doesn't match sum of status counts (${recordTypeStatusSum})`,
              );
            }

            recordTypeTotal += recordType.total;
          }

          // Verify sum of record type totals matches connector total
          if (recordTypeTotal !== connector.total) {
            logger.warn(
              `Connector ${connector.connector}: Total (${connector.total}) doesn't match ` +
                `sum of record type totals (${recordTypeTotal})`,
            );
          }
        }
      };

      // Only run validation in development/testing environments
      if (process.env.NODE_ENV !== 'production') {
        validateStatConsistency(data);
      }

      logger.info(
        `Retrieved enhanced connector stats for organization: ${orgId}`,
      );

      res.status(200).json({
        success: true,
        data,
      });
      return;
    } catch (error) {
      const err =
        error instanceof Error ? error : new Error('Unknown error occurred');
      logger.error(`Error getting connector stats: ${err.message}`);
      next(err);
    }
  };

export const reindexAllRecords =
  (recordRelationService: RecordRelationService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const app = req.body.app;
      if (!userId || !orgId) {
        throw new BadRequestError('User not authenticated');
      }

      const allowedApps = ['ONEDRIVE', 'DRIVE', 'GMAIL', 'CONFLUENCE', 'SLACK'];
      if (!allowedApps.includes(app)) {
        throw new BadRequestError('APP not allowed');
      }

      const reindexPayload = {
        userId,
        orgId,
        app,
      };

      const reindexResponse =
        await recordRelationService.reindexAllRecords(reindexPayload);

      res.status(200).json({
        reindexResponse,
      });

      return; // Added return statement
    } catch (error: any) {
      logger.error('Error re indexing all records', {
        error,
      });
      next(error);
      return; // Added return statement
    }
  };

export const resyncConnectorRecords =
  (recordRelationService: RecordRelationService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const connectorName = req.body.connectorName;
      if (!userId || !orgId) {
        throw new BadRequestError('User not authenticated');
      }

      const allowedConnectors = [
        'ONEDRIVE',
        'DRIVE',
        'GMAIL',
        'CONFLUENCE',
        'SLACK',
      ];
      if (!allowedConnectors.includes(connectorName)) {
        throw new BadRequestError(`Connector ${connectorName} not allowed`);
      }

      const resyncConnectorPayload = {
        userId,
        orgId,
        connectorName,
      };

      const resyncConnectorResponse =
        await recordRelationService.resyncConnectorRecords(
          resyncConnectorPayload,
        );

      res.status(200).json({
        resyncConnectorResponse,
      });

      return; // Added return statement
    } catch (error: any) {
      logger.error('Error resyncing connector records', {
        error,
      });
      next(error);
      return; // Added return statement
    }
  };

// export const restoreRecord =
//   (arangoService: ArangoService) =>
//   async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
//     try {
//     } catch (error: any) {
//       logger.error('Error restoring record', error);
//       next(error);
//     }
//   };

// export const setRecordExpirationTime =
//   (arangoService: ArangoService) =>
//   async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
//     try {
//     } catch (error: any) {
//       logger.error('Error setting record expiration time', error);
//       next(error);
//     }
//   };

// export const getOCRData =
//   (arangoService: ArangoService) =>
//   async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
//     try {
//     } catch (error: any) {
//       logger.error('Error getting OCR data', error);
//       next(error);
//     }
//   };

// export const uploadNextVersion =
//   (arangoService: ArangoService) =>
//   async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
//     try {
//     } catch (error: any) {
//       logger.error('Error uploading next version', error);
//       next(error);
//     }
//   };

// export const searchInKB =
//   (arangoService: ArangoService) =>
//   async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
//     try {
//     } catch (error: any) {
//       logger.error('Error searching in KB', error);
//       next(error);
//     }
//   };

// export const answerQueryFromKB =
//   (arangoService: ArangoService) =>
//   async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
//     try {
//     } catch (error: any) {
//       logger.error('Error answering query from KB', error);
//       next(error);
//     }
//   };
