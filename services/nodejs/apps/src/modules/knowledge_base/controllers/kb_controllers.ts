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
import { IServiceRecordsResponse } from '../types/service.records.response';
import axios from 'axios';
import { ArangoService } from '../../../libs/services/arango.service';

const logger = Logger.getInstance({
  service: 'Knowledge Base Controller',
});
const AI_SERVICE_UNAVAILABLE_MESSAGE =
  'AI Service is currently unavailable. Please check your network connection or try again later.';

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
        const { originalname, mimetype, size } = file;
        const extension = originalname.includes('.')
          ? originalname
              .substring(originalname.lastIndexOf('.') + 1)
              .toLowerCase()
          : null;

        // Generate a unique ID for the record
        const key: string = uuidv4();

        const webUrl = `/record/${key}`;

        const record = {
          _key: key,
          orgId: orgId,
          recordName: '',
          externalRecordId: '',
          recordType: RECORD_TYPE.FILE,
          origin: ORIGIN_TYPE.UPLOAD,
          createdAtTimestamp: currentTime,
          updatedAtTimestamp: currentTime,
          isDeleted: false,
          isArchived: false,
          indexingStatus: INDEXING_STATUS.NOT_STARTED,
          version: 1,
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
      let originalname, mimetype, size;

      if (hasFileBuffer) {
        ({ originalname, mimetype, size } = req.body.fileBuffer);
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
        sizeInBytes: size,
      };

      // Handle file uploads if present
      let fileUploaded = false;
      let fileName = '';

      // Handle file uploads if we found files
      if (hasFileBuffer) {
        // Use the externalRecordId as the storageDocumentId
        const storageDocumentId = existingRecord.record.externalRecordId;

        // Check if we have a valid externalRecordId to use
        if (!storageDocumentId) {
          throw new BadRequestError(
            'Cannot update file: No external record ID found for this record',
          );
        }

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

        // Log the file upload
        logger.info('Uploading new version of file', {
          recordId,
          fileName: originalname,
          fileSize: size,
          mimeType: mimetype,
          storageDocumentId: storageDocumentId,
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
          // Log the file upload
          logger.info('Uploading new version function called successfully');
          // Version will be auto-incremented in the service method
          // but we can explicitly set it here too
          updatedData.version = (existingRecord.record.version || 0) + 1;
          fileUploaded = true;
        } catch (storageError: any) {
          logger.error('Failed to upload file to storage', {
            recordId,
            storageDocumentId: storageDocumentId,
            error: storageError.message,
          });
          throw new InternalServerError(
            `Failed to upload file: ${storageError.message}`,
          );
        }
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

      // Update the record in the database
      const updatedRecord = await recordRelationService.updateRecord(
        recordId,
        updatedData,
        keyValueStoreService,
      );

      // Log the successful update
      logger.info('Record updated successfully', {
        recordId,
        userId,
        orgId,
        fileUploaded,
        newFileName: fileUploaded ? fileName : undefined,
        updatedFields: Object.keys(updatedData),
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
export const deleteRecord =
  (recordRelationService: RecordRelationService) =>
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

      // Check if user has permission to delete records
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
      // const time = Date.now();
      // Perform soft delete
      // const softDeleteData = {
      //   isDeleted: true,
      //   deletedByUserId: userId,
      //   // deletedAtTimestamp: time,
      //   updatedAtTimestamp: time,
      //   isLatestVersion: true,
      // };

      // Update the record for soft delete
      // await recordRelationService.updateRecord(recordId, softDeleteData);
      await recordRelationService.softDeleteRecord(recordId, userId);
      // Log the successful deletion
      logger.info('Record soft-deleted successfully', {
        recordId,
        userId,
        orgId,
        requestId: req.context?.requestId,
      });

      // Return success response
      res.status(200).json({
        message: 'Record deleted successfully',
        meta: {
          requestId: req.context?.requestId,
          timestamp: new Date().toISOString(),
        },
      });
    } catch (error: any) {
      // Log the error for debugging
      logger.error('Error deleting record', {
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
      const baseFilter = `doc.orgId == "${orgId}"`;

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
              not_supported= SUM(doc.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" ? 1 : 0)
            RETURN {
              total,
              indexing_status: {
                NOT_STARTED: not_started,
                IN_PROGRESS: in_progress,
                COMPLETED: completed,
                FAILED: failed,
                FILE_TYPE_NOT_SUPPORTED: not_supported
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
              not_supported= SUM(doc.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" ? 1 : 0)
            RETURN {
              total,
              indexing_status: {
                NOT_STARTED: not_started,
                IN_PROGRESS: in_progress,
                COMPLETED: completed,
                FAILED: failed,
                FILE_TYPE_NOT_SUPPORTED: not_supported
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
              not_supported= SUM(doc.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" ? 1 : 0)
            RETURN {
              total,
              indexing_status: {
                NOT_STARTED: not_started,
                IN_PROGRESS: in_progress,
                COMPLETED: completed,
                FAILED: failed,
                FILE_TYPE_NOT_SUPPORTED: not_supported
              }
            }
        )[0]

        // Enhanced connector stats with record type breakdowns
        LET connector_data = (
          FOR doc IN records
            FILTER ${baseFilter} AND doc.origin == "CONNECTOR"
            COLLECT connector = doc.connectorName INTO groupDocs = doc
            
            // Basic stats for this connector
            LET basic_stats = (
              FOR d IN groupDocs
                COLLECT AGGREGATE
                  total         = COUNT(1),
                  not_started   = SUM(d.indexingStatus == "NOT_STARTED" ? 1 : 0),
                  in_progress   = SUM(d.indexingStatus == "IN_PROGRESS" ? 1 : 0),
                  completed     = SUM(d.indexingStatus == "COMPLETED" ? 1 : 0),
                  failed        = SUM(d.indexingStatus == "FAILED" ? 1 : 0),
                  not_supported = SUM(d.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" ? 1 : 0)
                RETURN {
                  total,
                  indexing_status: {
                    NOT_STARTED: not_started,
                    IN_PROGRESS: in_progress,
                    COMPLETED: completed,
                    FAILED: failed,
                    FILE_TYPE_NOT_SUPPORTED: not_supported
                  }
                }
            )[0]
            
            // Record type breakdown for this connector
            LET record_types = (
              FOR d IN groupDocs
                COLLECT record_type = d.recordType INTO typeGroupDocs = d
                LET type_stats = (
                  FOR td IN typeGroupDocs
                    COLLECT AGGREGATE
                      total         = COUNT(1),
                      not_started   = SUM(td.indexingStatus == "NOT_STARTED" ? 1 : 0),
                      in_progress   = SUM(td.indexingStatus == "IN_PROGRESS" ? 1 : 0),
                      completed     = SUM(td.indexingStatus == "COMPLETED" ? 1 : 0),
                      failed        = SUM(td.indexingStatus == "FAILED" ? 1 : 0),
                      not_supported = SUM(td.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" ? 1 : 0)
                    RETURN {
                      total,
                      indexing_status: {
                        NOT_STARTED: not_started,
                        IN_PROGRESS: in_progress,
                        COMPLETED: completed,
                        FAILED: failed,
                        FILE_TYPE_NOT_SUPPORTED: not_supported
                      }
                    }
                )[0]
                RETURN {
                  record_type,
                  total: type_stats.total,
                  indexing_status: type_stats.indexing_status
                }
            )
            
            RETURN {
              connector,
              total: basic_stats.total,
              indexing_status: basic_stats.indexing_status,
              by_record_type: record_types
            }
        )

        // Stats by record type across all connectors
        LET record_type_stats = (
          FOR doc IN records
            FILTER ${baseFilter} AND doc.origin == "CONNECTOR"
            COLLECT record_type = doc.recordType INTO groupDocs = doc
            
            // Basic stats for this record type
            LET basic_stats = (
              FOR d IN groupDocs
                COLLECT AGGREGATE
                  total         = COUNT(1),
                  not_started   = SUM(d.indexingStatus == "NOT_STARTED" ? 1 : 0),
                  in_progress   = SUM(d.indexingStatus == "IN_PROGRESS" ? 1 : 0),
                  completed     = SUM(d.indexingStatus == "COMPLETED" ? 1 : 0),
                  failed        = SUM(d.indexingStatus == "FAILED" ? 1 : 0),
                  not_supported = SUM(d.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" ? 1 : 0)
                RETURN {
                  total,
                  indexing_status: {
                    NOT_STARTED: not_started,
                    IN_PROGRESS: in_progress,
                    COMPLETED: completed,
                    FAILED: failed,
                    FILE_TYPE_NOT_SUPPORTED: not_supported
                  }
                }
            )[0]
            
            // Connector breakdown for this record type
            LET connectors_for_type = (
              FOR d IN groupDocs
                COLLECT connector = d.connectorName INTO connectorGroupDocs = d
                LET connector_type_stats = (
                  FOR cd IN connectorGroupDocs
                    COLLECT AGGREGATE
                      total         = COUNT(1),
                      not_started   = SUM(cd.indexingStatus == "NOT_STARTED" ? 1 : 0),
                      in_progress   = SUM(cd.indexingStatus == "IN_PROGRESS" ? 1 : 0),
                      completed     = SUM(cd.indexingStatus == "COMPLETED" ? 1 : 0),
                      failed        = SUM(cd.indexingStatus == "FAILED" ? 1 : 0),
                      not_supported = SUM(cd.indexingStatus == "FILE_TYPE_NOT_SUPPORTED" ? 1 : 0)
                    RETURN {
                      total,
                      indexing_status: {
                        NOT_STARTED: not_started,
                        IN_PROGRESS: in_progress,
                        COMPLETED: completed,
                        FAILED: failed,
                        FILE_TYPE_NOT_SUPPORTED: not_supported
                      }
                    }
                )[0]
                RETURN {
                  connector,
                  total: connector_type_stats.total,
                  indexing_status: connector_type_stats.indexing_status
                }
            )
            
            RETURN {
              record_type,
              total: basic_stats.total,
              indexing_status: basic_stats.indexing_status,
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
