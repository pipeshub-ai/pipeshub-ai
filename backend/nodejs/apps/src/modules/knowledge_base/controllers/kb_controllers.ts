import { v4 as uuidv4 } from 'uuid';
import * as crypto from 'crypto';
import { AuthenticatedUserRequest } from './../../../libs/middlewares/types';
import { NextFunction, Response } from 'express';
import { Logger } from '../../../libs/services/logger.service';
import { RecordRelationService } from '../services/kb.relation.service';
import {
  BadRequestError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import {
  uploadNextVersionToStorage,
  createPlaceholderDocument,
  processUploadsInBackground,
  FileUploadMetadata,
  PlaceholderResultWithMetadata,
} from '../utils/utils';
import { IRecordDocument } from '../types/record';
import { IFileRecordDocument } from '../types/file_record';
import {
  INDEXING_STATUS,
  ORIGIN_TYPE,
  RECORD_TYPE,
} from '../constants/record.constants';
import axios from 'axios';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { getMimeType } from '../../storage/mimetypes/mimetypes';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  executeConnectorCommand,
  handleBackendError,
  handleConnectorResponse,
} from '../../tokens_manager/utils/connector.utils';
import { NotificationService } from '../../notification/service/notification.service';
import {
  safeParsePagination,
} from '../../../utils/safe-integer';
import { validateNoFormatSpecifiers, validateNoXSS } from '../../../utils/xss-sanitization';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
const logger = Logger.getInstance({
  service: 'Knowledge Base Controller',
});

/**
 * Get Knowledge Hub nodes (unified browse API)
 * Supports browsing KBs, apps, folders, record groups, and records
 */
export const getKnowledgeHubNodes =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};
      if (!userId || !orgId) {
        throw new UnauthorizedError('User not authenticated');
      }

      logger.info('Getting knowledge hub nodes', {
        userId,
        orgId,
        query: req.query,
      });

      // Build query string from request query params
      const queryParams = new URLSearchParams();

      // Map query params (camelCase to snake_case for Python backend)
      const paramMapping: { [key: string]: string } = {
        parentId: 'parent_id',
        view: 'view',
        page: 'page',
        limit: 'limit',
        sortBy: 'sort_by',
        sortOrder: 'sort_order',
        q: 'q',
        nodeTypes: 'node_types',
        recordTypes: 'record_types',
        origins: 'origins',
        connectorIds: 'connector_ids',
        kbIds: 'kb_ids',
        indexingStatus: 'indexing_status',
        createdAt: 'created_at',
        updatedAt: 'updated_at',
        size: 'size',
        include: 'include',
      };

      for (const [key, snakeKey] of Object.entries(paramMapping)) {
        const value = req.query[key];
        if (value) {
          queryParams.append(snakeKey, value as string);
        }
      }

      if (req.query.onlyContainers !== undefined) {
        queryParams.append(
          'only_containers',
          String(req.query.onlyContainers),
        );
      }

      const { parentType, parentId } = req.params;
      let url = `${appConfig.connectorBackend}/api/v1/knowledge-hub/nodes`;

      if (parentType && parentId) {
        url += `/${parentType}/${parentId}`;
      }

      url += `?${queryParams.toString()}`;

      const response = await executeConnectorCommand(
        url,
        HttpMethod.GET,
        req.headers as Record<string, string>, // Forwards auth headers
      );

      handleConnectorResponse(
        response,
        res,
        'Getting knowledge hub nodes',
        'Failed to get nodes',
      );
    } catch (error: any) {
      logger.error('Error getting knowledge hub nodes', {
        error: error.message,
        stack: error.stack,
      });
      const handleError = handleBackendError(
        error,
        'get knowledge hub nodes',
      );
      next(handleError);
    }
  };

// Types and helpers for active connector validation
interface ConnectorInfo {
  _key: string;
}

interface ActiveConnectorsResponse {
  connectors: ConnectorInfo[];
}

const normalizeAppName = (value: string): string =>
  value.replace(' ', '').toLowerCase();

const validateActiveConnector = async (
  connectorId: string,
  appConfig: AppConfig,
  headers: Record<string, string>,
): Promise<void> => {
  const activeAppsResponse = await executeConnectorCommand(
    `${appConfig.connectorBackend}/api/v1/connectors/active`,
    HttpMethod.GET,
    headers,
  );

  if (activeAppsResponse.statusCode !== 200) {
    throw new InternalServerError('Failed to get active connectors');
  }

  const data = activeAppsResponse.data as ActiveConnectorsResponse;
  const connectors = data?.connectors || [];

  const isAllowed = connectors.some((connector) => connector._key === connectorId);

  if (!isAllowed) {
    throw new BadRequestError(`Connector ${connectorId} not allowed`);
  }

  logger.debug('Connector validation successful', {
    connectorId,
  });
};

export const createKnowledgeBase =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};
      const { kbName } = req.body;

      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication required');
      }

      // Validate kbName for XSS and format specifiers
      if (kbName) {
        validateNoXSS(kbName, 'Knowledge base name');
        validateNoFormatSpecifiers(kbName, 'Knowledge base name');
      }

      logger.info(`Creating knowledge base '${kbName}' for user ${userId}`);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        {
          name: kbName,
        },
      );

      handleConnectorResponse(
        response,
        res,
        'Creating knowledge base',
        'Knowledge base creation failed',
      );
      logger.info(`Knowledge base '${kbName}' created successfully`);
    } catch (error: any) {
      logger.error('Error creating knowledge base', { error: error.message });
      const handleError = handleBackendError(error, 'create knowledge base');
      next(handleError);
    }
  };

export const getKnowledgeBase =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { kbId } = req.params;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      logger.info(`Getting knowledge base ${kbId} for user ${userId}`);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        response,
        res,
        'Getting knowledge base',
        'Knowledge base not found',
      );
    } catch (error: any) {
      logger.error('Error getting knowledge base', {
        error: error.message,
        kbId: req.params.kbId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'get knowledge base');
      next(handleError);
    }
  };

export const listKnowledgeBases =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};

      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication required');
      }

      // Extract and parse query parameters with safe integer validation
      let page: number;
      let limit: number;
      try {
        const pagination = safeParsePagination(
          req.query.page as string | undefined,
          req.query.limit as string | undefined,
          1,
          20,
          100,
        );
        page = pagination.page;
        limit = pagination.limit;
      } catch (error: any) {
        throw new BadRequestError(
          error.message || 'Invalid pagination parameters',
        );
      }

      const search = req.query.search ? String(req.query.search) : undefined;
      
      // Additional validation for search parameter (defense in depth)
      if (search) {
        try {
          validateNoXSS(search, 'search parameter');
          validateNoFormatSpecifiers(search, 'search parameter');
          
          if (search.length > 1000) {
            throw new BadRequestError('Search parameter too long (max 1000 characters)');
          }
        } catch (error: any) {
          throw new BadRequestError(
            error.message || 'Search parameter contains potentially dangerous content'
          );
        }
      }
      
      const permissions = req.query.permissions
        ? String(req.query.permissions).split(',')
        : undefined;
      const sortBy = req.query.sortBy ? String(req.query.sortBy) : 'name';
      const sortOrder = req.query.sortOrder
        ? String(req.query.sortOrder)
        : 'asc';

      // Validate sort parameters
      const validSortFields = [
        'name',
        'createdAtTimestamp',
        'updatedAtTimestamp',
        'userRole',
      ];
      if (!validSortFields.includes(sortBy)) {
        throw new BadRequestError(
          `Invalid sort field. Must be one of: ${validSortFields.join(', ')}`,
        );
      }

      const validSortOrders = ['asc', 'desc'];
      if (!validSortOrders.includes(sortOrder.toLowerCase())) {
        throw new BadRequestError(
          `Invalid sort order. Must be one of: ${validSortOrders.join(', ')}`,
        );
      }

      // Validate permissions filter
      if (permissions) {
        const validPermissions = [
          'OWNER',
          'ORGANIZER',
          'FILEORGANIZER',
          'WRITER',
          'COMMENTER',
          'READER',
        ];
        const invalidPermissions = permissions.filter(
          (p) => !validPermissions.includes(p),
        );
        if (invalidPermissions.length > 0) {
          throw new BadRequestError(
            `Invalid permissions: ${invalidPermissions.join(', ')}`,
          );
        }
      }

      logger.info(
        `Listing knowledge bases for user ${userId} with pagination`,
        {
          page,
          limit,
          search,
          permissions,
          sortBy,
          sortOrder,
        },
      );

      const queryParams = new URLSearchParams();
      if (page) queryParams.set('page', String(page));
      if (limit) queryParams.set('limit', String(limit));
      if (search) queryParams.set('search', String(search));
      if (permissions)
        queryParams.set('permissions', String(permissions?.join(',')));
      if (sortBy) queryParams.set('sort_by', String(sortBy));
      if (sortOrder) queryParams.set('sort_order', String(sortOrder));

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/?${queryParams.toString()}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        response,
        res,
        'Getting knowledge bases',
        'Knowledge bases not found',
      );

      // Log successful retrieval
      logger.debug('Knowledge bases retrieved successfully');
    } catch (error: any) {
      logger.error('Error listing knowledge bases', {
        error: error.message,
        userId: req.user?.userId,
        orgId: req.user?.orgId,
      });
      const handleError = handleBackendError(error, 'list knowledge bases');
      next(handleError);
    }
  };

export const updateKnowledgeBase =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { kbId } = req.params;
      const { kbName } = req.body;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      // Validate kbName for XSS and format specifiers
      if (kbName) {
        validateNoXSS(kbName, 'Knowledge base name');
        validateNoFormatSpecifiers(kbName, 'Knowledge base name');
      }

      logger.info(`Updating knowledge base ${kbId}`);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}`,
        HttpMethod.PUT,
        req.headers as Record<string, string>,
        {
          groupName: kbName,
        },
      );

      handleConnectorResponse(
        response,
        res,
        'Updating knowledge base',
        'Knowledge base not found',
      );
    } catch (error: any) {
      logger.error('Error updating knowledge base', { error: error.message });
      const handleError = handleBackendError(error, 'update knowledge base');
      next(handleError);
    }
  };

export const deleteKnowledgeBase =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { kbId } = req.params;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }
      logger.info(`Deleting knowledge base ${kbId}`);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}`,
        HttpMethod.DELETE,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        response,
        res,
        'Deleting knowledge base',
        'Knowledge base not found',
      );
    } catch (error: any) {
      logger.error('Error deleting knowledge base', { error: error.message });
      const handleError = handleBackendError(error, 'delete knowledge base');
      next(handleError);
    }
  };

export const createRootFolder =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};
      const { kbId } = req.params;
      const { folderName } = req.body;

      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication required');
      }

      // Validate folderName for XSS and format specifiers
      if (folderName) {
        validateNoXSS(folderName, 'Folder name');
        validateNoFormatSpecifiers(folderName, 'Folder name');
      }

      logger.info(`Creating folder '${folderName}' in KB ${kbId}`);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/folder`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        {
          name: folderName,
        },
      );

      handleConnectorResponse(
        response,
        res,
        'Creating folder',
        'Folder not found',
      );
    } catch (error: any) {
      logger.error('Error creating folder for knowledge base', {
        error: error.message,
        kbId: req.params.kbId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'create root folder');
      next(handleError);
    }
  };

export const createNestedFolder =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};
      const { kbId, folderId } = req.params;
      const { folderName } = req.body;

      if (!userId || !orgId) {
        throw new UnauthorizedError('User authentication required');
      }

      // Validate folderName for XSS and format specifiers
      if (folderName) {
        validateNoXSS(folderName, 'Folder name');
        validateNoFormatSpecifiers(folderName, 'Folder name');
      }

      logger.info(`Creating folder '${folderName}' in folder ${folderId}`);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/folder/${folderId}/subfolder`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        {
          name: folderName,
        },
      );

      handleConnectorResponse(
        response,
        res,
        'Creating nested folder',
        'Folder not found',
      );
    } catch (error: any) {
      logger.error('Error creating subfolder folder', {
        error: error.message,
        kbId: req.params.kbId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'create nested folder');
      next(handleError);
    }
  };

export const updateFolder =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { kbId, folderId } = req.params;
      const { folderName } = req.body;
      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      // Validate folderName for XSS and format specifiers
      if (folderName) {
        validateNoXSS(folderName, 'Folder name');
        validateNoFormatSpecifiers(folderName, 'Folder name');
      }

      logger.info(`Updating folder ${folderId} in KB ${kbId}`);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/folder/${folderId}`,
        HttpMethod.PUT,
        req.headers as Record<string, string>,
        { name: folderName },
      );

      handleConnectorResponse(
        response,
        res,
        'Updating folder',
        'Folder not found',
      );
    } catch (error: any) {
      logger.error('Error updating folder for knowledge base', {
        error: error.message,
        kbId: req.params.kbId,
        folderId: req.params.folderId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'update folder');
      next(handleError);
    }
  };

export const deleteFolder =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { kbId, folderId } = req.params;
      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      logger.info(`Deleting folder ${folderId} in KB ${kbId}`);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/folder/${folderId}`,
        HttpMethod.DELETE,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        response,
        res,
        'Deleting folder',
        'Folder not found',
      );
    } catch (error: any) {
      logger.error('Error deleting folder for knowledge base', {
        error: error.message,
        kbId: req.params.kbId,
        folderId: req.params.folderId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'delete folder');
      next(handleError);
    }
  };

/**
 * Upload records to Knowledge Base.
 * Files are processed by file processor middleware which attaches
 * filePath and lastModified to each file buffer.
 */
export const uploadRecordsToKB =
  (
    keyValueStoreService: KeyValueStoreService,
    appConfig: AppConfig,
    notificationService?: NotificationService,
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const fileBuffers: FileBufferInfo[] = req.body.fileBuffers || [];
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const { kbId } = req.params;
      const isVersioned = req.body?.isVersioned ?? true;

      // Validation
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      if (!kbId || fileBuffers.length === 0) {
        throw new BadRequestError('Knowledge Base ID and files are required');
      }

      logger.info('Processing file upload to KB', {
        totalFiles: fileBuffers.length,
        kbId,
        userId,
        samplePaths: fileBuffers.slice(0, 3).map((f) => f.filePath),
      });

      const currentTime = Date.now();

      // STEP 1: Create all placeholder documents first (fast operation)
      // Track successful and failed files separately
      const placeholderResults: PlaceholderResultWithMetadata[] = [];
      const failedFiles: Array<{
        fileName: string;
        filePath: string;
        error: string;
      }> = [];

      for (const file of fileBuffers) {
        const { originalname, mimetype, size, filePath, lastModified } = file;

        // Extract filename from path
        const fileName = filePath.includes('/')
          ? filePath.split('/').pop() || originalname
          : filePath;

        const extension = fileName.includes('.')
          ? fileName.substring(fileName.lastIndexOf('.') + 1).toLowerCase()
          : null;

        // Use correct MIME type mapping instead of browser detection
        const correctMimeType =
          (extension && getMimeType(extension)) || mimetype;

        // Generate unique ID for the record
        const key: string = uuidv4();
        const webUrl = `/record/${key}`;

        const validLastModified =
          lastModified && !isNaN(lastModified) && lastModified > 0
            ? lastModified
            : currentTime;

        // Create file metadata structure
        const metadata: FileUploadMetadata = {
          file,
          filePath,
          fileName,
          extension,
          correctMimeType,
          key,
          webUrl,
          validLastModified,
          size,
        };

        // Create placeholder document (fast, doesn't upload file yet)
        // Wrap in try-catch to handle individual file failures
        try {
          const placeholderResult = await createPlaceholderDocument(
            req,
            file,
            fileName,
            isVersioned,
            keyValueStoreService,
            appConfig.storage,
          );

          placeholderResults.push({
            placeholderResult,
            metadata,
          });
        } catch (placeholderError: any) {
          // Track failed file but continue processing others
          // Handle different error response structures
          const errorMessage = placeholderError?.response?.data?.error?.message ||
                              placeholderError?.response?.data?.message || 
                              placeholderError?.message || 
                              'Failed to create placeholder document';
          
          failedFiles.push({
            fileName,
            filePath,
            error: errorMessage,
          });

          logger.error('Failed to create placeholder document for file', {
            fileName,
            filePath,
            error: errorMessage,
            stack: placeholderError?.stack,
          });
          // Continue with next file
        }
      }

      logger.info('✅ Placeholder creation completed', {
        totalFiles: fileBuffers.length,
        successful: placeholderResults.length,
        failed: failedFiles.length,
        uploadsRequired: placeholderResults.filter((r) => r.placeholderResult.uploadPromise).length,
      });

      // Send failed files notification immediately if any failed during placeholder creation
      // Event-driven: Send immediately when failures are detected
      // NotificationService handles connection state and queuing automatically
      if (failedFiles.length > 0 && notificationService) {
        try {
          const failedFilesWithIds = failedFiles.map((ff) => ({
            recordId: uuidv4(), // Generate a temporary ID for tracking
            fileName: ff.fileName,
            filePath: ff.filePath,
            error: ff.error,
          }));

          const eventData = {
            failedFiles: failedFilesWithIds,
            orgId,
            kbId,
            folderId: undefined,
            totalFailed: failedFiles.length,
            timestamp: Date.now(),
          };

          // Send immediately - service handles connection state and queuing
          const sentImmediately = notificationService.sendToUser(userId, 'records:failed', eventData);

          logger.info('Notification sent for failed files (placeholder creation)', {
            userId,
            totalFailed: failedFiles.length,
            kbId,
            sentImmediately,
          });
        } catch (socketError: unknown) {
          const error = socketError instanceof Error ? socketError : new Error(String(socketError));
          logger.error('Failed to send notification for failed files (placeholder creation)', {
            error: error.message,
            stack: error.stack,
            userId,
            kbId,
          });
          // Don't fail the upload if notification fails - this is a non-critical notification
        }
      }

      // If no files succeeded, return early with error info
      if (placeholderResults.length === 0) {
        res.status(200).json({
          message: 'All files failed to upload',
          totalFiles: fileBuffers.length,
          status: 'failed',
          records: [],
          failedFiles: failedFiles.map((ff) => ({
            fileName: ff.fileName,
            filePath: ff.filePath,
            error: ff.error,
          })),
        });
        return;
      }

      // STEP 2: Build placeholder records for optimistic UI update (Google Drive-like experience)
      const placeholderRecords = placeholderResults.map((result) => {
        const { placeholderResult, metadata } = result;
        const { extension, correctMimeType, key, webUrl, validLastModified, size } = metadata;

        // Create record structure matching the format expected by frontend
        // Create record structure
        const connectorId = `knowledgeBase_${orgId}`;
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
          connectorId: connectorId,
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

      // STEP 3: Return response to frontend immediately with placeholder records (unblock frontend)
      // Frontend can display these records immediately with a "processing" status
      res.status(200).json({
        message: failedFiles.length > 0 
          ? `Upload initiated: ${placeholderResults.length} succeeded, ${failedFiles.length} failed`
          : 'Upload initiated successfully',
        totalFiles: fileBuffers.length,
        successfulFiles: placeholderResults.length,
        failedFiles: failedFiles.length,
        status: 'processing',
        failedFilesDetails: failedFiles.map((ff) => ({
          fileName: ff.fileName,
          filePath: ff.filePath,
          error: ff.error,
        })),
        records: placeholderRecords.map((pr) => ({
          _key: pr.record._key,
          recordName: pr.record.recordName,
          externalRecordId: pr.record.externalRecordId,
          recordType: pr.record.recordType,
          origin: pr.record.origin,
          indexingStatus: 'NOT_STARTED', // Special status to indicate background not started
          createdAtTimestamp: pr.record.createdAtTimestamp,
          updatedAtTimestamp: pr.record.updatedAtTimestamp,
          sourceCreatedAtTimestamp: pr.record.sourceCreatedAtTimestamp,
          sourceLastModifiedTimestamp: pr.record.sourceLastModifiedTimestamp,
          version: pr.record.version,
          webUrl: pr.record.webUrl,
          mimeType: pr.record.mimeType,
          fileRecord: {
            _key: pr.fileRecord._key,
            name: pr.fileRecord.name,
            extension: pr.fileRecord.extension,
            mimeType: pr.fileRecord.mimeType,
            sizeInBytes: pr.fileRecord.sizeInBytes,
            webUrl: pr.fileRecord.webUrl,
          },
        })),
      });

      // STEP 4: Process uploads and call Python service in background (non-blocking)
      // This runs after the response is sent - uploads sequentially, then calls Python
      // For local storage, files are already uploaded, so this will just call Python API
      processUploadsInBackground(
        placeholderResults,
        orgId,
        userId,
        currentTime,
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/upload`,
        req.headers as Record<string, string>,
        logger,
        notificationService,
        kbId,
        undefined, // folderId is undefined for KB root uploads
      ).catch((error) => {
        logger.error('Background processing error (non-fatal)', {
          error: error.message,
          stack: error.stack,
          kbId,
          userId,
        });
      });
    } catch (error: any) {
      logger.error('Record upload failed', {
        error: error.message,
        userId: req.user?.userId,
        kbId: req.params.kbId,
      });
      const backendError = handleBackendError(error, 'Record upload api');
      next(backendError);
    }
  };

/**
 * Upload records to a specific folder within a Knowledge Base.
 * Files are processed by file processor middleware which attaches
 * filePath and lastModified to each file buffer.
 */
export const uploadRecordsToFolder =
  (
    keyValueStoreService: KeyValueStoreService,
    appConfig: AppConfig,
    notificationService?: NotificationService,
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const fileBuffers: FileBufferInfo[] = req.body.fileBuffers || [];
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const { kbId, folderId } = req.params;
      const isVersioned = req.body?.isVersioned ?? true;

      // Validation
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      if (!kbId || !folderId || fileBuffers.length === 0) {
        throw new BadRequestError(
          'Knowledge Base ID, Folder ID, and files are required',
        );
      }

      logger.info('Processing file upload to folder', {
        totalFiles: fileBuffers.length,
        kbId,
        folderId,
        userId,
        samplePaths: fileBuffers.slice(0, 3).map((f) => f.filePath),
      });

      const currentTime = Date.now();

      // STEP 1: Create all placeholder documents first (fast operation)
      // Track successful and failed files separately
      const placeholderResults: PlaceholderResultWithMetadata[] = [];
      const failedFiles: Array<{
        fileName: string;
        filePath: string;
        error: string;
      }> = [];

      for (const file of fileBuffers) {
        const { originalname, mimetype, size, filePath, lastModified } = file;

        // Extract filename from path
        const fileName = filePath.includes('/')
          ? filePath.split('/').pop() || originalname
          : filePath;

        const extension = fileName.includes('.')
          ? fileName.substring(fileName.lastIndexOf('.') + 1).toLowerCase()
          : null;

        // Use correct MIME type mapping instead of browser detection
        const correctMimeType =
          (extension && getMimeType(extension)) || mimetype;

        // Generate unique ID for the record
        const key: string = uuidv4();
        const webUrl = `/record/${key}`;

        const validLastModified =
          lastModified && !isNaN(lastModified) && lastModified > 0
            ? lastModified
            : currentTime;

        // Create file metadata structure
        const metadata: FileUploadMetadata = {
          file,
          filePath,
          fileName,
          extension,
          correctMimeType,
          key,
          webUrl,
          validLastModified,
          size,
        };

        // Create placeholder document (fast, doesn't upload file yet)
        // Wrap in try-catch to handle individual file failures
        try {
          const placeholderResult = await createPlaceholderDocument(
            req,
            file,
            fileName,
            isVersioned,
            keyValueStoreService,
            appConfig.storage,
          );

          placeholderResults.push({
            placeholderResult,
            metadata,
          });
        } catch (placeholderError: any) {
          // Track failed file but continue processing others
          // Handle different error response structures
          const errorMessage = placeholderError?.response?.data?.error?.message ||
                              placeholderError?.response?.data?.message || 
                              placeholderError?.message || 
                              'Failed to create placeholder document';
          
          failedFiles.push({
            fileName,
            filePath,
            error: errorMessage,
          });

          logger.error('Failed to create placeholder document for file', {
            fileName,
            filePath,
            error: errorMessage,
            stack: placeholderError?.stack,
          });
          // Continue with next file
        }
      }

      logger.info('✅ Placeholder creation completed', {
        totalFiles: fileBuffers.length,
        successful: placeholderResults.length,
        failed: failedFiles.length,
        uploadsRequired: placeholderResults.filter((r) => r.placeholderResult.uploadPromise).length,
      });

      // Send failed files notification immediately if any failed during placeholder creation
      // Event-driven: Send immediately when failures are detected
      // NotificationService handles connection state and queuing automatically
      if (failedFiles.length > 0 && notificationService) {
        try {
          const failedFilesWithIds = failedFiles.map((ff) => ({
            recordId: uuidv4(), // Generate a temporary ID for tracking
            fileName: ff.fileName,
            filePath: ff.filePath,
            error: ff.error,
          }));

          const eventData = {
            failedFiles: failedFilesWithIds,
            orgId,
            kbId,
            folderId,
            totalFailed: failedFiles.length,
            timestamp: Date.now(),
          };

          // Send immediately - service handles connection state and queuing
          const sentImmediately = notificationService.sendToUser(userId, 'records:failed', eventData);

          logger.info('Notification sent for failed files (placeholder creation)', {
            userId,
            totalFailed: failedFiles.length,
            kbId,
            folderId,
            sentImmediately,
          });
        } catch (socketError: unknown) {
          const error = socketError instanceof Error ? socketError : new Error(String(socketError));
          logger.error('Failed to send notification for failed files (placeholder creation)', {
            error: error.message,
            stack: error.stack,
            userId,
            kbId,
            folderId,
          });
          // Don't fail the upload if notification fails - this is a non-critical notification
        }
      }

      // If no files succeeded, return early with error info
      if (placeholderResults.length === 0) {
        res.status(200).json({
          message: 'All files failed to upload',
          totalFiles: fileBuffers.length,
          status: 'failed',
          records: [],
          failedFiles: failedFiles.map((ff) => ({
            fileName: ff.fileName,
            filePath: ff.filePath,
            error: ff.error,
          })),
        });
        return;
      }

      // STEP 2: Build placeholder records for optimistic UI update (Google Drive-like experience)
      const placeholderRecords = placeholderResults.map((result) => {
        const { placeholderResult, metadata } = result;
        const { extension, correctMimeType, key, webUrl, validLastModified, size } = metadata;

        // Create record structure matching the format expected by frontend
        // Create record structure
        const connectorId = `knowledgeBase_${orgId}`;
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
          connectorId: connectorId,
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

      // STEP 3: Return response to frontend immediately with placeholder records (unblock frontend)
      // Frontend can display these records immediately with a "processing" status
      res.status(200).json({
        message: failedFiles.length > 0 
          ? `Upload initiated: ${placeholderResults.length} succeeded, ${failedFiles.length} failed`
          : 'Upload initiated successfully',
        totalFiles: fileBuffers.length,
        successfulFiles: placeholderResults.length,
        failedFiles: failedFiles.length,
        status: 'processing',
        failedFilesDetails: failedFiles.map((ff) => ({
          fileName: ff.fileName,
          filePath: ff.filePath,
          error: ff.error,
        })),
        records: placeholderRecords.map((pr) => ({
          _key: pr.record._key,
          recordName: pr.record.recordName,
          externalRecordId: pr.record.externalRecordId,
          recordType: pr.record.recordType,
          origin: pr.record.origin,
          indexingStatus: 'NOT_STARTED', // Special status to indicate background not started
          createdAtTimestamp: pr.record.createdAtTimestamp,
          updatedAtTimestamp: pr.record.updatedAtTimestamp,
          sourceCreatedAtTimestamp: pr.record.sourceCreatedAtTimestamp,
          sourceLastModifiedTimestamp: pr.record.sourceLastModifiedTimestamp,
          version: pr.record.version,
          webUrl: pr.record.webUrl,
          mimeType: pr.record.mimeType,
          fileRecord: {
            _key: pr.fileRecord._key,
            name: pr.fileRecord.name,
            extension: pr.fileRecord.extension,
            mimeType: pr.fileRecord.mimeType,
            sizeInBytes: pr.fileRecord.sizeInBytes,
            webUrl: pr.fileRecord.webUrl,
          },
        })),
      });

      // STEP 4: Process uploads and call Python service in background (non-blocking)
      // This runs after the response is sent - uploads sequentially, then calls Python
      // For local storage, files are already uploaded, so this will just call Python API
      processUploadsInBackground(
        placeholderResults,
        orgId,
        userId,
        currentTime,
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/folder/${folderId}/upload`,
        req.headers as Record<string, string>,
        logger,
        notificationService,
        kbId,
        folderId,
      ).catch((error) => {
        logger.error('Background processing error (non-fatal)', {
          error: error.message,
          stack: error.stack,
          kbId,
          folderId,
          userId,
        });
      });
    } catch (error: any) {
      logger.error('❌ Folder record upload failed', {
        error: error.message,
        userId: req.user?.userId,
        kbId: req.params.kbId,
        folderId: req.params.folderId,
      });
      const backendError = handleBackendError(
        error,
        'Folder record upload api',
      );
      next(backendError);
    }
  };

export const updateRecord =
  (keyValueStoreService: KeyValueStoreService, appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { recordId } = req.params;
      const { userId, orgId } = req.user || {};
      let { recordName } = req.body || {};

      if (!userId || !orgId) {
        throw new BadRequestError('User authentication is required');
      }

      // Check if there's a file in the request
      const hasFileBuffer = req.body.fileBuffer && req.body.fileBuffer.buffer;
      let originalname, mimetype, size, extension, lastModified, sha256Hash;

      if (hasFileBuffer) {
        ({ originalname, mimetype, size, lastModified } = req.body.fileBuffer);

        // Extract extension from filename
        extension = originalname.includes('.')
          ? originalname
              .substring(originalname.lastIndexOf('.') + 1)
              .toLowerCase()
          : null;
        // Calculate SHA-256 checksum for security
        const buffer = req.body.fileBuffer.buffer;
        sha256Hash = crypto.createHash('sha256').update(buffer).digest('hex');
      }

      if (!recordName) {
        recordName = originalname;
        logger.info('No custom name provided');
      }

      // Validate recordName for XSS and format specifiers
      // This validation happens after we've determined the final recordName value
      if (recordName) {
        validateNoXSS(recordName, 'Record name');
        validateNoFormatSpecifiers(recordName, 'Record name');
      }

      // Prepare update data with timestamp
      const updatedData = {
        recordName,
      };

      // Add file-related data if file is being uploaded
      let fileMetadata = null;
      if (hasFileBuffer) {
        fileMetadata = {
          originalname,
          mimetype,
          size,
          extension,
          lastModified,
          sha256Hash,
        };

        // Get filename without extension to use as record name
        if (originalname && originalname.includes('.')) {
          const lastDotIndex = originalname.lastIndexOf('.');
          if (lastDotIndex > 0) {
            const fileNameWithoutExt = originalname.substring(0, lastDotIndex);
            // Validate the filename (without extension) for XSS
            validateNoXSS(fileNameWithoutExt, 'Record name');
            validateNoFormatSpecifiers(fileNameWithoutExt, 'Record name');
            updatedData.recordName = fileNameWithoutExt;
            logger.info('Setting record name from file', {
              recordName: updatedData.recordName,
              originalFileName: originalname,
            });
          }
        }
      }

      // STEP 1: Update the record in the database FIRST (before storage)
      logger.info('Updating record in database via Python service', {
        recordId,
        hasFileUpload: hasFileBuffer,
        updatedFields: Object.keys(updatedData),
      });

      // Call the Python service to update the record
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/record/${recordId}`,
        HttpMethod.PUT,
        req.headers as Record<string, string>,
        {
          updates: updatedData,
          fileMetadata: fileMetadata,
        },
      );

      const updateRecordResponse = response.data as any;

      if (!updateRecordResponse) {
        throw new InternalServerError(
          'Python service indicated failure to update record',
        );
      }

      if (!updateRecordResponse.updatedRecord) {
        throw new InternalServerError(
          'Python service indicated failure to update record',
        );
      }

      const updateResult = updateRecordResponse.updatedRecord;

      // STEP 2: Upload file to storage ONLY after database update succeeds
      let fileUploaded = false;
      let storageDocumentId = null;

      if (hasFileBuffer && updateResult) {
        // Use the externalRecordId as the storageDocumentId
        storageDocumentId = updateResult.externalRecordId;

        // Check if we have a valid externalRecordId to use
        if (!storageDocumentId) {
          logger.error('No external record ID found after database update', {
            recordId,
            updatedRecord: updateResult?._key,
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
          version: updateResult?.version,
        });

        try {
          // Update version through storage service using externalRecordId
          const fileBuffer = req.body.fileBuffer;
          await uploadNextVersionToStorage(
            req,
            fileBuffer,
            storageDocumentId,
            keyValueStoreService,
            appConfig.storage, // Fixed: use appConfig.storage instead of defaultConfig
          );

          logger.info('File uploaded to storage successfully', {
            recordId,
            storageDocumentId,
            version: updateResult?.version,
          });

          fileUploaded = true;
        } catch (storageError: any) {
          const is404 = storageError?.response?.status === 404;
          
          logger.error(
            'Failed to upload file to storage after database update',
            {
              recordId,
              storageDocumentId: storageDocumentId,
              error: storageError.message,
              version: updateResult.version,
              is404,
            },
          );

          // Log the inconsistent state but don't fail the request
          // since the database update was successful
          logger.warn(
            'Database updated but storage upload failed - inconsistent state',
            {
              recordId,
              storageDocumentId,
              databaseVersion: updateResult.version,
            },
          );

          // Provide specific error message for 404 (file not found in storage)
          if (is404) {
            throw new InternalServerError(
              `File storage document not found. The original file may have been deleted` +
              `Please delete this record and re-upload the file.`,
            );
          }

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
        newFileName: fileUploaded ? originalname : undefined,
        updatedFields: Object.keys(updatedData),
        version: updateResult?.version,
        requestId: req.context?.requestId,
      });

      // Return the updated record
      res.status(200).json({
        message: fileUploaded
          ? 'Record updated with new file version'
          : 'Record updated successfully',
        record: updateResult,
        fileUploaded,
        meta: {
          requestId: req.context?.requestId,
          timestamp: new Date().toISOString(),
        },
      });
    } catch (error: any) {
      // Log the error for debugging
      logger.error('Error updating folder record', {
        recordId: req.params.recordId,
        kbId: req.params.kbId,
        error: error.message,
        stack: error.stack,
        userId: req.user?.userId,
        orgId: req.user?.orgId,
        requestId: req.context?.requestId,
      });
      const handleError = handleBackendError(error, 'update record');
      next(handleError);
    }
  };

/**
 * Get records for a specific Knowledge Base
 */
export const getKBContent =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      // Extract user from request
      const { userId, orgId } = req.user || {};
      const { kbId } = req.params;

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      if (!kbId) {
        throw new BadRequestError('Knowledge Base ID is required');
      }

      // Extract and parse query parameters with safe integer validation
      let page: number;
      let limit: number;
      try {
        const pagination = safeParsePagination(
          req.query.page as string | undefined,
          req.query.limit as string | undefined,
          1,
          20,
          100,
        );
        page = pagination.page;
        limit = pagination.limit;
      } catch (error: any) {
        throw new BadRequestError(
          error.message || 'Invalid pagination parameters',
        );
      }

      const search = req.query.search ? String(req.query.search) : undefined;
      
      // Validate search parameter for XSS and format specifiers
      if (search) {
        try {
          validateNoXSS(search, 'search parameter');
          validateNoFormatSpecifiers(search, 'search parameter');
          
          if (search.length > 1000) {
            throw new BadRequestError('Search parameter too long (max 1000 characters)');
          }
        } catch (error: any) {
          throw new BadRequestError(
            error.message || 'Search parameter contains potentially dangerous content'
          );
        }
      }
      
      const recordTypes = req.query.recordTypes
        ? String(req.query.recordTypes).split(',')
        : undefined;
      const origins = req.query.origins
        ? String(req.query.origins).split(',')
        : undefined;
      const connectors = req.query.connectors
        ? String(req.query.connectors).split(',')
        : undefined;
      const indexingStatus = req.query.indexingStatus
        ? String(req.query.indexingStatus).split(',')
        : undefined;

      // Parse date filters with safe integer validation
      let dateFrom: number | undefined;
      let dateTo: number | undefined;
      if (req.query.dateFrom) {
        try {
          dateFrom = parseInt(String(req.query.dateFrom), 10);
          if (isNaN(dateFrom) || dateFrom < 0) {
            throw new BadRequestError('Invalid dateFrom parameter');
          }
        } catch (error: any) {
          throw new BadRequestError('Invalid dateFrom parameter');
        }
      }
      if (req.query.dateTo) {
        try {
          dateTo = parseInt(String(req.query.dateTo), 10);
          if (isNaN(dateTo) || dateTo < 0) {
            throw new BadRequestError('Invalid dateTo parameter');
          }
        } catch (error: any) {
          throw new BadRequestError('Invalid dateTo parameter');
        }
      }

      // Sorting parameters
      const sortBy = req.query.sortBy
        ? String(req.query.sortBy)
        : 'createdAtTimestamp';
      const sortOrderParam = req.query.sortOrder
        ? String(req.query.sortOrder)
        : 'desc';
      const sortOrder =
        sortOrderParam === 'asc' || sortOrderParam === 'desc'
          ? sortOrderParam
          : 'desc';

      logger.info('Getting KB records', {
        kbId,
        userId,
        orgId,
        page,
        limit,
        search,
        recordTypes,
        origins,
        connectors,
        indexingStatus,
        dateFrom,
        dateTo,
        sortBy,
        sortOrder,
        requestId: req.context?.requestId,
      });

      const queryParams = new URLSearchParams();
      if (page) {
        queryParams.append('page', page.toString());
      }
      if (limit) {
        queryParams.append('limit', limit.toString());
      }
      if (search) {
        queryParams.append('search', search);
      }
      if (recordTypes) {
        queryParams.append('record_types', recordTypes.join(','));
      }
      if (origins) {
        queryParams.append('origins', origins.join(','));
      }
      if (connectors) {
        queryParams.append('connectors', connectors.join(','));
      }
      if (indexingStatus) {
        queryParams.append('indexing_status', indexingStatus.join(','));
      }
      if (dateFrom) {
        queryParams.append('date_from', dateFrom.toString());
      }
      if (dateTo) {
        queryParams.append('date_to', dateTo.toString());
      }
      if (sortBy) {
        queryParams.append('sort_by', sortBy);
      }
      if (sortOrder) {
        queryParams.append('sort_order', sortOrder);
      }

      // Call the Python service to get KB records
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/children?${queryParams.toString()}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        response,
        res,
        'Getting KB records',
        'KB records not found',
      );

      // Log successful retrieval
      logger.info('KB records retrieved successfully', kbId);
    } catch (error: any) {
      logger.error('Error getting KB records', {
        kbId: req.params.kbId,
        userId: req.user?.userId,
        orgId: req.user?.orgId,
        error: error.message,
        stack: error.stack,
        requestId: req.context?.requestId,
      });
      const handleError = handleBackendError(error, 'get KB records');
      next(handleError);
    }
  };

/**
 * Get records for a specific Knowledge Base
 */
export const getFolderContents =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      // Extract user from request
      const { userId, orgId } = req.user || {};
      const { kbId, folderId } = req.params;

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      if (!kbId) {
        throw new BadRequestError('Knowledge Base ID is required');
      }

      // Extract and parse query parameters with safe integer validation
      let page: number;
      let limit: number;
      try {
        const pagination = safeParsePagination(
          req.query.page as string | undefined,
          req.query.limit as string | undefined,
          1,
          20,
          100,
        );
        page = pagination.page;
        limit = pagination.limit;
      } catch (error: any) {
        throw new BadRequestError(
          error.message || 'Invalid pagination parameters',
        );
      }

      const search = req.query.search ? String(req.query.search) : undefined;
      
      // Validate search parameter for XSS and format specifiers
      if (search) {
        try {
          validateNoXSS(search, 'search parameter');
          validateNoFormatSpecifiers(search, 'search parameter');
          
          if (search.length > 1000) {
            throw new BadRequestError('Search parameter too long (max 1000 characters)');
          }
        } catch (error: any) {
          throw new BadRequestError(
            error.message || 'Search parameter contains potentially dangerous content'
          );
        }
      }
      
      const recordTypes = req.query.recordTypes
        ? String(req.query.recordTypes).split(',')
        : undefined;
      const origins = req.query.origins
        ? String(req.query.origins).split(',')
        : undefined;
      const connectors = req.query.connectors
        ? String(req.query.connectors).split(',')
        : undefined;
      const indexingStatus = req.query.indexingStatus
        ? String(req.query.indexingStatus).split(',')
        : undefined;

      // Parse date filters with safe integer validation
      let dateFrom: number | undefined;
      let dateTo: number | undefined;
      if (req.query.dateFrom) {
        try {
          dateFrom = parseInt(String(req.query.dateFrom), 10);
          if (isNaN(dateFrom) || dateFrom < 0) {
            throw new BadRequestError('Invalid dateFrom parameter');
          }
        } catch (error: any) {
          throw new BadRequestError('Invalid dateFrom parameter');
        }
      }
      if (req.query.dateTo) {
        try {
          dateTo = parseInt(String(req.query.dateTo), 10);
          if (isNaN(dateTo) || dateTo < 0) {
            throw new BadRequestError('Invalid dateTo parameter');
          }
        } catch (error: any) {
          throw new BadRequestError('Invalid dateTo parameter');
        }
      }

      // Sorting parameters
      const sortBy = req.query.sortBy
        ? String(req.query.sortBy)
        : 'createdAtTimestamp';
      const sortOrderParam = req.query.sortOrder
        ? String(req.query.sortOrder)
        : 'desc';
      const sortOrder =
        sortOrderParam === 'asc' || sortOrderParam === 'desc'
          ? sortOrderParam
          : 'desc';

      logger.info('Getting KB records', {
        kbId,
        userId,
        orgId,
        page,
        limit,
        search,
        recordTypes,
        origins,
        connectors,
        indexingStatus,
        dateFrom,
        dateTo,
        sortBy,
        sortOrder,
        requestId: req.context?.requestId,
      });

      const queryParams = new URLSearchParams();
      if (page) {
        queryParams.append('page', page.toString());
      }
      if (limit) {
        queryParams.append('limit', limit.toString());
      }
      if (search) {
        queryParams.append('search', search);
      }
      if (recordTypes) {
        queryParams.append('record_types', recordTypes.join(','));
      }
      if (origins) {
        queryParams.append('origins', origins.join(','));
      }
      if (connectors) {
        queryParams.append('connectors', connectors.join(','));
      }
      if (indexingStatus) {
        queryParams.append('indexing_status', indexingStatus.join(','));
      }
      if (dateFrom) {
        queryParams.append('date_from', dateFrom.toString());
      }
      if (dateTo) {
        queryParams.append('date_to', dateTo.toString());
      }
      if (sortBy) {
        queryParams.append('sort_by', sortBy);
      }
      if (sortOrder) {
        queryParams.append('sort_order', sortOrder);
      }

      // Call the Python service to get KB records
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/folder/${folderId}/children?${queryParams.toString()}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        response,
        res,
        'Getting folder contents',
        'Folder contents not found',
      );

      // Log successful retrieval
      logger.info('KB records retrieved successfully', kbId);
    } catch (error: any) {
      logger.error('Error getting KB records', {
        kbId: req.params.kbId,
        userId: req.user?.userId,
        orgId: req.user?.orgId,
        error: error.message,
        stack: error.stack,
        requestId: req.context?.requestId,
      });
      const handleError = handleBackendError(error, 'get KB records');
      next(handleError);
    }
  };

/**
 * Get all records accessible to user across all Knowledge Bases
 */
export const getAllRecords =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      // Extract user from request
      const { userId, orgId } = req.user || {};

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      // Extract and parse query parameters
      const page = req.query.page ? parseInt(String(req.query.page), 10) : 1;
      const limit = req.query.limit
        ? parseInt(String(req.query.limit), 10)
        : 20;
      const search = req.query.search ? String(req.query.search) : undefined;
      
      // Validate search parameter for XSS and format specifiers
      if (search) {
        try {
          validateNoXSS(search, 'search parameter');
          validateNoFormatSpecifiers(search, 'search parameter');
          
          if (search.length > 1000) {
            throw new BadRequestError('Search parameter too long (max 1000 characters)');
          }
        } catch (error: any) {
          throw new BadRequestError(
            error.message || 'Search parameter contains potentially dangerous content'
          );
        }
      }
      
      const recordTypes = req.query.recordTypes
        ? String(req.query.recordTypes).split(',')
        : undefined;
      const origins = req.query.origins
        ? String(req.query.origins).split(',')
        : undefined;
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
      const sortBy = req.query.sortBy
        ? String(req.query.sortBy)
        : 'createdAtTimestamp';
      const sortOrderParam = req.query.sortOrder
        ? String(req.query.sortOrder)
        : 'desc';
      const sortOrder =
        sortOrderParam === 'asc' || sortOrderParam === 'desc'
          ? sortOrderParam
          : 'desc';

      // Parse source parameter
      const source = req.query.source
        ? ['all', 'local', 'connector'].includes(String(req.query.source))
          ? (String(req.query.source) as 'all' | 'local' | 'connector')
          : 'all'
        : 'all';

      // Validate pagination parameters
      if (page < 1) {
        throw new BadRequestError('Page must be greater than 0');
      }
      if (limit < 1 || limit > 100) {
        throw new BadRequestError('Limit must be between 1 and 100');
      }

      logger.debug('Getting all records for user', {
        userId,
        orgId,
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
        requestId: req.context?.requestId,
      });

      const queryParams = new URLSearchParams();

      if (page) {
        queryParams.append('page', page.toString());
      }
      if (limit) {
        queryParams.append('limit', limit.toString());
      }
      if (search) {
        queryParams.append('search', search);
      }
      if (recordTypes) {
        queryParams.append('record_types', recordTypes.join(','));
      }
      if (origins) {
        queryParams.append('origins', origins.join(','));
      }
      if (connectors) {
        queryParams.append('connectors', connectors.join(','));
      }
      if (permissions) {
        queryParams.append('permissions', permissions.join(','));
      }
      if (indexingStatus) {
        queryParams.append('indexing_status', indexingStatus.join(','));
      }
      if (dateFrom) {
        queryParams.append('date_from', dateFrom.toString());
      }
      if (dateTo) {
        queryParams.append('date_to', dateTo.toString());
      }
      if (sortBy) {
        queryParams.append('sort_by', sortBy);
      }
      if (sortOrder) {
        queryParams.append('sort_order', sortOrder);
      }
      if (source) {
        queryParams.append('source', source);
      }

      // Call the Python service to get all records
      const response = await executeConnectorCommand(
        // `${appConfig.connectorBackend}/api/v1/kb/records/user/${userId}/org/${orgId}`,
        `${appConfig.connectorBackend}/api/v1/records?${queryParams.toString()}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      if (response.statusCode !== 200) {
        throw new InternalServerError('Failed to get all records');
      }

      const result = response.data as any;

      // Log successful retrieval
      logger.debug('All records retrieved successfully', {
        totalRecords: result.pagination?.totalCount || 0,
        page: result.pagination?.page || page,
        userId,
        orgId,
        source,
        requestId: req.context?.requestId,
      });

      // Send response
      res.status(200).json({
        records: result.records || [],
        pagination: {
          page: result.pagination?.page || page,
          limit: result.pagination?.limit || limit,
          totalCount: result.pagination?.totalCount || 0,
          totalPages: result.pagination?.totalPages || 0,
        },
        filters: {
          applied: {
            search,
            recordTypes,
            origins,
            connectors,
            permissions,
            indexingStatus,
            source: source !== 'all' ? source : null,
            dateRange:
              dateFrom || dateTo ? { from: dateFrom, to: dateTo } : null,
            sortBy,
            sortOrder,
          },
          available: result.filters?.available || {},
        },
      });
    } catch (error: any) {
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
      logger.error('Error getting all records', {
        userId: req.user?.userId,
        orgId: req.user?.orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
        stack: error instanceof Error ? error.stack : undefined,
        requestId: req.context?.requestId,
      });
      const handleError = handleBackendError(error, 'get all records');
      next(handleError);
    }
  };

export const getRecordById =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { recordId } = req.params as { recordId: string };
      const { userId, orgId } = req.user || {};

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      // Call the Python service to get record
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/records/${recordId}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        response,
        res,
        'Getting record by id',
        'Record not found',
      );

      // Log successful retrieval
      logger.info('Record retrieved successfully');
    } catch (error: any) {
      logger.error('Error getting record by id', {
        recordId: req.params.recordId,
        error,
      });
      const handleError = handleBackendError(error, 'get record by id');
      next(handleError);
      return; // Added return statement
    }
  };

export const reindexRecord =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { recordId } = req.params as { recordId: string };
      const { userId, orgId } = req.user || {};
      const { depth = 0, force = false } = req.body || {};

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      // Call the Python service to reindex record
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/records/${recordId}/reindex`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        { depth, force },
      );

      handleConnectorResponse(
        response,
        res,
        'Record not found',
        'Record not reindexed',
      );

      // Log successful reindex
      logger.info('Record reindexed successfully', { force });
    } catch (error: any) {
      logger.error('Error reindexing record', {
        recordId: req.params.recordId,
        error,
      });
      const handleError = handleBackendError(error, 'reindex record');
      next(handleError);
      return;
    }
  };

export const reindexRecordGroup =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { recordGroupId } = req.params as { recordGroupId: string };
      const { userId, orgId } = req.user || {};
      const { depth = 0, force = false } = req.body || {};

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      // Call the Python service to reindex record group
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/record-groups/${recordGroupId}/reindex`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        { depth, force },
      );

      handleConnectorResponse(
        response,
        res,
        'Record group not found',
        'Record group not reindexed',
      );

      // Log successful reindex
      logger.info('Record group reindexed successfully', {
        recordGroupId,
        depth,
        force,
      });
    } catch (error: any) {
      logger.error('Error reindexing record group', {
        recordGroupId: req.params.recordGroupId,
        error,
      });
      const handleError = handleBackendError(error, 'reindex record group');
      next(handleError);
      return;
    }
  };

export const deleteRecord =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { recordId } = req.params as { recordId: string };
      const { userId, orgId } = req.user || {};

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      // Call the Python service to get record
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/records/${recordId}`,
        HttpMethod.DELETE,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        response,
        res,
        'Deleting record',
        'Record not deleted',
      );

      // Log successful retrieval
      logger.info('Record deleted successfully');
    } catch (error: any) {
      logger.error('Error deleting record', {
        recordId: req.params.recordId,
        error,
      });
      next(error);
      return; // Added return statement
    }
  };

/**
 * Create permissions for multiple users on a knowledge base
 */
export const createKBPermission =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { kbId } = req.params;
      const { userIds, teamIds, role } = req.body;

      if (userIds.length === 0 && teamIds.length === 0) {
        throw new BadRequestError('User IDs or team IDs are required');
      }

      // Role is required only if users are provided (teams don't need roles)
      if (userIds.length > 0 && !role) {
        throw new BadRequestError('Role is required when adding users');
      }

      // Validate role only if it's provided (for users)
      if (role) {
        const validRoles = [
          'OWNER',
          'ORGANIZER',
          'FILEORGANIZER',
          'WRITER',
          'COMMENTER',
          'READER',
        ];
        if (!validRoles.includes(role)) {
          throw new BadRequestError(
            `Invalid role. Must be one of: ${validRoles.join(', ')}`,
          );
        }
      }

      logger.info(
        `Creating ${role || 'team'} permissions for ${userIds.length} users and ${teamIds.length} teams on KB ${kbId}`,
      );

      const payload: { userIds: string[]; teamIds: string[]; role?: string } = {
        userIds: userIds,
        teamIds: teamIds,
      };
      // Only include role if it's provided (for users)
      if (role) {
        payload.role = role;
      }

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/permissions`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        payload,
      );

      if (response.statusCode !== 200 && response.statusCode !== 201) {
        throw handleBackendError(response, 'create permissions');
      }

      const permissionResult = response.data as any;
      if (!permissionResult) {
        throw new NotFoundError('Failed to create permissions');
      }

      res.status(201).json({
        kbId: kbId,
        permissionResult,
      });
    } catch (error: any) {
      logger.error('Error creating KB permissions', {
        error: error.message,
        kbId: req.params.kbId,
      });
      const handleError = handleBackendError(error, 'create permissions');
      next(handleError);
    }
  };

/**
 * Update a single user's permission on a knowledge base
 */
export const updateKBPermission =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { kbId } = req.params;
      const { userIds, teamIds, role } = req.body;

      if (userIds.length === 0 && teamIds.length === 0) {
        throw new BadRequestError('User IDs or team IDs are required');
      }

      if (!role) {
        throw new BadRequestError('Role is required');
      }

      const validRoles = [
        'OWNER',
        'ORGANIZER',
        'FILEORGANIZER',
        'WRITER',
        'COMMENTER',
        'READER',
      ];
      if (!validRoles.includes(role)) {
        throw new BadRequestError(
          `Invalid role. Must be one of: ${validRoles.join(', ')}`,
        );
      }

      logger.info(
        `Updating permission for ${userIds.length} users and ${teamIds.length} teams on KB ${kbId} to ${role}`,
      );

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/permissions`,
        HttpMethod.PUT,
        req.headers as Record<string, string>,
        {
          userIds: userIds,
          teamIds: teamIds,
          role: role,
        },
      );

      if (response.statusCode !== 200) {
        throw handleBackendError(response, 'update permissions');
      }

      const updateResult = response.data as any;

      res.status(200).json({
        kbId: kbId,
        userIds: updateResult.userIds,
        teamIds: updateResult.teamIds,
        newRole: updateResult.newRole,
      });
    } catch (error: any) {
      logger.error('Error updating KB permission', {
        error: error.message,
        kbId: req.params.kbId,
      });
      const handleError = handleBackendError(error, 'update permissions');
      next(handleError);
    }
  };

/**
 * Remove a user's permission from a knowledge base
 */
export const removeKBPermission =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { kbId } = req.params;
      const { userIds, teamIds } = req.body;

      if (userIds.length === 0 && teamIds.length === 0) {
        throw new BadRequestError('User IDs or team IDs are required');
      }

      logger.info(
        `Removing permission for ${userIds.length} users and ${teamIds.length} teams from KB ${kbId}`,
      );

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/permissions`,
        HttpMethod.DELETE,
        req.headers as Record<string, string>,
        {
          userIds: userIds,
          teamIds: teamIds,
        },
      );

      if (response.statusCode !== 200) {
        throw handleBackendError(response, 'remove permissions');
      }

      const removeResult = response.data as any;

      res.status(200).json({
        kbId: kbId,
        userIds: removeResult.userIds,
        teamIds: removeResult.teamIds,
      });
    } catch (error: any) {
      logger.error('Error removing KB permission', {
        error: error.message,
        kbId: req.params.kbId,
      });
      const handleError = handleBackendError(error, 'removing KB permissions');
      next(handleError);
    }
  };

/**
 * List all permissions for a knowledge base
 */
export const listKBPermissions =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { kbId } = req.params;

      logger.info(`Listing permissions for KB ${kbId}`);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/permissions`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      if (response.statusCode !== 200) {
        throw handleBackendError(response, 'list permissions');
      }

      const listResult = response.data as any;

      res.status(200).json({
        kbId: kbId,
        permissions: listResult.permissions,
        totalCount: listResult.totalCount,
      });
    } catch (error: any) {
      logger.error('Error listing KB permissions', {
        error: error.message,
        kbId: req.params.kbId,
      });
      const handleError = handleBackendError(error, 'list permissions');
      next(handleError);
    }
  };

export const getConnectorStats =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { userId, orgId } = req.user || {};

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      if (!req.params.connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      try {
        // Call the Python service to get record

        const queryParams = new URLSearchParams();

        queryParams.append('org_id', orgId);
        queryParams.append('connector_id', req.params.connectorId);
        const response = await executeConnectorCommand(
          `${appConfig.connectorBackend}/api/v1/stats?${queryParams.toString()}`,
          HttpMethod.GET,
          req.headers as Record<string, string>,
        );

        if (response.statusCode !== 200) {
          throw new InternalServerError(
            'Failed to get connector stats via Python service',
          );
        }

        const result = response.data;

        // Log successful retrieval
        logger.info('Connector stats retrieved successfully', {
          userId,
          orgId,
          requestId: req.context?.requestId,
        });

        // Send response
        res.status(200).json(result);
      } catch (pythonServiceError: any) {
        logger.error('Error calling Python service for record', {
          userId,
          orgId,
          error: pythonServiceError.message,
          response: pythonServiceError.response?.data,
          requestId: req.context?.requestId,
        });

        // Handle different error types from Python service
        if (pythonServiceError.response?.status === 403) {
          throw new ForbiddenError(
            'You do not have permission to access connector stats',
          );
        } else if (pythonServiceError.response?.status === 404) {
          throw new NotFoundError('No records found or user not found');
        } else if (pythonServiceError.response?.status === 400) {
          throw new BadRequestError(
            pythonServiceError.response?.data?.reason ||
              'Invalid request parameters',
          );
        } else {
          throw new InternalServerError(
            `Failed to get connector stats: ${pythonServiceError.message}`,
          );
        }
      }
    } catch (error: any) {
      logger.error('Error getting connector stats', {
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
      const { convertTo } = req.query as { convertTo: string };
      if (!userId || !orgId) {
        throw new BadRequestError('User authentication is required');
      }

      const queryParams = new URLSearchParams();
      if (convertTo) {
        logger.info('Converting file to ', { convertTo });
        queryParams.append('convertTo', convertTo);
      }

      // Make request to FastAPI backend
      const response = await axios.get(
        `${connectorUrl}/api/v1/stream/record/${recordId}?${queryParams.toString()}`,
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
      if (response.headers['content-type']) {
        res.set('Content-Type', response.headers['content-type']);
      }
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
          try {
            res.status(500).end('Error streaming data');
          } catch (e) {
            logger.error('Failed to send stream error response to client', {
              error: e,
            });
          }
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
          // Don't throw here to avoid uncaughtException shutdown during streams
          res.status(500).json({ error: 'Failed to retrieve record data' });
          return;
        }
      }
      const handleError = handleBackendError(error, 'get record buffer');
      logger.error('Error fetching record buffer', {
        error: error.message,
      });
      next(handleError);
    }
  };

export const reindexFailedRecords =
  (recordRelationService: RecordRelationService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const app = req.body.app;
      const connectorId = req.body.connectorId;
      if (!userId || !orgId) {
        throw new BadRequestError('User not authenticated');
      }

      await validateActiveConnector(
        connectorId,
        appConfig,
        req.headers as Record<string, string>,
      );

      const reindexPayload = {
        userId,
        orgId,
        app: normalizeAppName(app),
        connectorId,
        statusFilters: req.body.statusFilters,
      };

      const reindexResponse =
        await recordRelationService.reindexFailedRecords(reindexPayload);

      res.status(200).json({
        reindexResponse,
      });

      return; // Added return statement
    } catch (error: any) {
      logger.error('Error re indexing failed records', {
        error,
      });
      next(error);
      return; // Added return statement
    }
  };

export const resyncConnectorRecords =
  (recordRelationService: RecordRelationService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const connectorName = req.body.connectorName;
      const connectorId = req.body.connectorId;
      const fullSync = req.body.fullSync || false;
      if (!userId || !orgId) {
        throw new BadRequestError('User not authenticated');
      }

      await validateActiveConnector(
        connectorId,
        appConfig,
        req.headers as Record<string, string>,
      );

      const resyncConnectorPayload = {
        userId,
        orgId,
        connectorName: normalizeAppName(connectorName),
        connectorId,
        fullSync,
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

/**
 * Move a record (file or folder) to a different location within the same KB.
 */
export const moveRecord =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { kbId, recordId } = req.params;
      const { newParentId } = req.body;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      logger.info(
        `Moving record ${recordId} to ${newParentId ? `folder ${newParentId}` : 'KB root'} in KB ${kbId}`,
      );

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/record/${recordId}/move`,
        HttpMethod.PUT,
        req.headers as Record<string, string>,
        { newParentId },
      );

      handleConnectorResponse(
        response,
        res,
        'Moving record',
        'Record not found',
      );
    } catch (error: any) {
      logger.error('Error moving record', {
        error: error.message,
        kbId: req.params.kbId,
        recordId: req.params.recordId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'move record');
      next(handleError);
    }
  };
