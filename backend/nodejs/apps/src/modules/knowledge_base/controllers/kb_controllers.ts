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
  ServiceUnavailableError,
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
} from '../constants/record.constants';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { getMimeType } from '../../storage/mimetypes/mimetypes';
import axios from 'axios';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  ConnectorServiceCommand,
  ConnectorServiceCommandOptions,
} from '../../../libs/commands/connector_service/connector.service.command';

const logger = Logger.getInstance({
  service: 'Knowledge Base Controller',
});

const CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE =
  'Connector Service is currently unavailable. Please check your network connection or try again later.';

const handleBackendError = (error: any, operation: string): Error => {
  if (
    (error?.cause && error.cause.code === 'ECONNREFUSED') ||
    (typeof error?.message === 'string' &&
      error.message.includes('fetch failed'))
  ) {
    return new ServiceUnavailableError(
      CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
      error,
    );
  }

  if (error.response) {
    const { status, data } = error.response;
    const errorDetail =
      data?.error?.message ||
      data?.detail ||
      data?.reason ||
      data?.message ||
      error?.message ||
      'Unknown error';

    logger.error(`Backend error during ${operation}`, {
      status,
      errorDetail,
      fullResponse: data,
    });

    if (errorDetail === 'ECONNREFUSED') {
      throw new InternalServerError(
        CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    switch (status) {
      case 400:
        return new BadRequestError(errorDetail);
      case 401:
        return new UnauthorizedError(errorDetail);
      case 403:
        return new ForbiddenError(errorDetail);
      case 404:
        return new NotFoundError(errorDetail);
      case 500:
        return new InternalServerError(errorDetail);
      default:
        return new InternalServerError(`Backend error: ${errorDetail}`);
    }
  }

  if (error.request) {
    logger.error(`No response from backend during ${operation}`);
    return new InternalServerError('Backend service unavailable');
  }

  return new InternalServerError(`${operation} failed: ${error.message}`);
};

const executeConnectorCommand = async (
  uri: string,
  method: HttpMethod,
  headers: Record<string, string>,
  body?: any,
) => {
  const connectorCommandOptions: ConnectorServiceCommandOptions = {
    uri,
    method,
    headers: {
      ...headers,
      'Content-Type': 'application/json',
    },
    ...(body && { body }),
  };
  const connectorCommand = new ConnectorServiceCommand(connectorCommandOptions);
  return await connectorCommand.execute();
};

const handleConnectorResponse = (
  connectorResponse: any,
  res: Response,
  notFoundMessage: string,
  failureMessage: string,
) => {
  if (
    connectorResponse &&
    connectorResponse.statusCode !== 200 &&
    connectorResponse.statusCode !== 201
  ) {
    throw new BadRequestError(failureMessage);
  }
  const connectorsData = connectorResponse.data;
  if (!connectorsData) {
    throw new NotFoundError(notFoundMessage);
  }
  res.status(200).json(connectorsData);
};

// Types and helpers for active connector validation
interface ConnectorInfo {
  name: string;
}

interface ActiveConnectorsResponse {
  connectors: ConnectorInfo[];
}

const normalizeAppName = (value: string): string => value.replace(' ', '').toLowerCase();

const validateActiveConnector = async (
  appName: string,
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
  const allowedApps = connectors.map((connector) => normalizeAppName(connector.name));

  if (!allowedApps.includes(normalizeAppName(appName))) {
    throw new BadRequestError(`Connector ${appName} not allowed`);
  }
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
        'Knowledge base not found',
        'Failed to create knowledge base',
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
        'Knowledge base not found',
        'Failed to get knowledge base',
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

      // Extract and parse query parameters
      const page = req.query.page ? parseInt(String(req.query.page), 10) : 1;
      const limit = req.query.limit
        ? parseInt(String(req.query.limit), 10)
        : 20;
      const search = req.query.search ? String(req.query.search) : undefined;
      const permissions = req.query.permissions
        ? String(req.query.permissions).split(',')
        : undefined;
      const sortBy = req.query.sortBy ? String(req.query.sortBy) : 'name';
      const sortOrder = req.query.sortOrder
        ? String(req.query.sortOrder)
        : 'asc';

      // Validate pagination parameters
      if (page < 1) {
        throw new BadRequestError('Page must be greater than 0');
      }
      if (limit < 1 || limit > 100) {
        throw new BadRequestError('Limit must be between 1 and 100');
      }

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
        'Knowledge base not found',
        'Failed to get knowledge bases',
      );

      // Log successful retrieval
      logger.info('Knowledge bases retrieved successfully');
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
        'Knowledge base not found',
        'Failed to update knowledge base',
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
        'Knowledge base not found',
        'Failed to delete knowledge base',
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
        'Folder not found',
        'Failed to create folder',
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
        'Folder not found',
        'Failed to create nested folder',
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
        'Folder not found',
        'Failed to update folder',
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
        'Folder not found',
        'Failed to delete folder',
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

//  Upload records in KB along with folder creation and folder record creation new controller
export const uploadRecordsToKB =
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
      const { kbId } = req.params; // should be sent in params instead of req.body
      const filePaths = req.body.file_paths || [];
      const lastModifiedTimes = req.body.last_modified || [];
      const isVersioned = req.body?.isVersioned || true;

      // Validation
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      if (!kbId || !files || files.length === 0) {
        throw new BadRequestError('Knowledge Base ID and files are required');
      }

      if (
        files.length !== filePaths.length ||
        files.length !== lastModifiedTimes.length
      ) {
        throw new BadRequestError(
          'Files, paths, and timestamps arrays must have the same length',
        );
      }

      console.log('📦 Processing optimized upload:', {
        totalFiles: files.length,
        kbId,
        userId,
        samplePaths: filePaths.slice(0, 3),
      });

      const currentTime = Date.now();

      // Process files and create records (storage operations)
      const processedFiles = [];

      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const filePath = filePaths[i];
        const lastModified = parseInt(lastModifiedTimes[i]) || currentTime;

        const { originalname, mimetype, size } = file;

        // Extract filename from path
        const fileName = filePath.includes('/')
          ? filePath.split('/').pop() || originalname
          : filePath;

        const extension = fileName.includes('.')
          ? fileName.substring(fileName.lastIndexOf('.') + 1).toLowerCase()
          : null;
        
        // Use correct MIME type mapping instead of browser detection
        const correctMimeType = (extension && getMimeType(extension)) || mimetype;
        // Generate unique ID for the record
        const key: string = uuidv4();
        const webUrl = `/record/${key}`;

        const validLastModified =
          lastModified && !isNaN(lastModified) && lastModified > 0
            ? lastModified
            : currentTime;

        // Create record structure
        const record: IRecordDocument = {
          _key: key,
          orgId: orgId,
          recordName: fileName,
          externalRecordId: '',
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
          name: fileName,
          isFile: true,
          extension: extension,
          mimeType: correctMimeType,
          sizeInBytes: size,
          webUrl: webUrl,
          // path: filePath,
        };

        // Save file to storage and get document ID
        const { documentId, documentName } =
          await saveFileToStorageAndGetDocumentId(
            req,
            file,
            fileName,
            isVersioned,
            record,
            fileRecord,
            keyValueStoreService,
            appConfig.storage,
            recordRelationService,
          );

        // Update record and fileRecord with storage info
        record.recordName = documentName;
        record.externalRecordId = documentId;
        fileRecord.name = documentName;

        processedFiles.push({
          record,
          fileRecord,
          filePath,
          lastModified: validLastModified,
        });
      }

      console.log('✅ Files processed, calling Python service');

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/upload`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        {
          files: processedFiles.map((pf) => ({
            record: pf.record,
            fileRecord: pf.fileRecord,
            filePath: pf.filePath,
            lastModified: pf.lastModified,
          })),
        },
      );

      handleConnectorResponse(
        response,
        res,
        'Upload not found',
        'Failed to process upload',
      );
    } catch (error: any) {
      console.error('❌ Record upload failed:', {
        error: error.message,
        userId: req.user?.userId,
        kbId: req.body.kb_id,
      });
      const backendError = handleBackendError(error, 'Record upload api');
      next(backendError);
    }
  };

export const uploadRecordsToFolder =
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
      const { kbId, folderId } = req.params;
      const filePaths = req.body.file_paths || [];
      const lastModifiedTimes = req.body.last_modified || [];
      const isVersioned = req.body?.isVersioned || true;

      // Validation
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      if (!kbId || !folderId || !files || files.length === 0) {
        throw new BadRequestError(
          'Knowledge Base ID, Folder ID, and files are required',
        );
      }

      if (
        files.length !== filePaths.length ||
        files.length !== lastModifiedTimes.length
      ) {
        throw new BadRequestError(
          'Files, paths, and timestamps arrays must have the same length',
        );
      }

      console.log('📦 Processing folder upload:', {
        totalFiles: files.length,
        kbId,
        folderId,
        userId,
        samplePaths: filePaths.slice(0, 3),
      });

      const currentTime = Date.now();

      // Process files and create records (storage operations)
      const processedFiles = [];

      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const filePath = filePaths[i];
        const lastModified = parseInt(lastModifiedTimes[i]) || currentTime;

        const { originalname, mimetype, size } = file;

        // Extract filename from path
        const fileName = filePath.includes('/')
          ? filePath.split('/').pop() || originalname
          : filePath;

        const extension = fileName.includes('.')
          ? fileName.substring(fileName.lastIndexOf('.') + 1).toLowerCase()
          : null;

        // Use correct MIME type mapping instead of browser detection
        const correctMimeType = extension ? getMimeType(extension) : mimetype;

        // Generate unique ID for the record
        const key: string = uuidv4();
        const webUrl = `/record/${key}`;

        const validLastModified =
          lastModified && !isNaN(lastModified) && lastModified > 0
            ? lastModified
            : currentTime;

        // Create record structure
        const record: IRecordDocument = {
          _key: key,
          orgId: orgId,
          recordName: fileName,
          externalRecordId: '',
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
          name: fileName,
          isFile: true,
          extension: extension,
          mimeType: correctMimeType,
          sizeInBytes: size,
          webUrl: webUrl,
          // path: filePath,
        };

        // Save file to storage and get document ID
        const { documentId, documentName } =
          await saveFileToStorageAndGetDocumentId(
            req,
            file,
            fileName,
            isVersioned,
            record,
            fileRecord,
            keyValueStoreService,
            appConfig.storage,
            recordRelationService,
          );

        // Update record and fileRecord with storage info
        record.recordName = documentName;
        record.externalRecordId = documentId;
        fileRecord.name = documentName;

        processedFiles.push({
          record,
          fileRecord,
          filePath,
          lastModified: validLastModified,
        });
      }

      console.log(
        '✅ Files processed, calling Python service for folder upload',
      );

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}/folder/${folderId}/upload`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        {
          files: processedFiles.map((pf) => ({
            record: pf.record,
            fileRecord: pf.fileRecord,
            filePath: pf.filePath,
            lastModified: pf.lastModified,
          })),
        },
      );

      handleConnectorResponse(
        response,
        res,
        'Upload not found',
        'Failed to process upload',
      );
    } catch (error: any) {
      console.error('❌ Folder record upload failed:', {
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
      let originalname, mimetype, size, extension, lastModified;

      if (hasFileBuffer) {
        ({ originalname, mimetype, size, lastModified } = req.body.fileBuffer);

        // Extract extension from filename
        extension = originalname.includes('.')
          ? originalname
              .substring(originalname.lastIndexOf('.') + 1)
              .toLowerCase()
          : null;
      }

      if (!recordName) {
        recordName = originalname;
        logger.info('No custom name provided');
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
        };

        // Get filename without extension to use as record name
        if (originalname && originalname.includes('.')) {
          const lastDotIndex = originalname.lastIndexOf('.');
          if (lastDotIndex > 0) {
            updatedData.recordName = originalname.substring(0, lastDotIndex);
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

      if (hasFileBuffer && updateResult?.record) {
        // Use the externalRecordId as the storageDocumentId
        storageDocumentId = updateResult.record.externalRecordId;

        // Check if we have a valid externalRecordId to use
        if (!storageDocumentId) {
          logger.error('No external record ID found after database update', {
            recordId,
            updatedRecord: updateResult?.record._key,
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
          version: updateResult?.record.version,
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
            version: updateResult?.record.version,
          });

          fileUploaded = true;
        } catch (storageError: any) {
          logger.error(
            'Failed to upload file to storage after database update',
            {
              recordId,
              storageDocumentId: storageDocumentId,
              error: storageError.message,
              version: updateResult.record.version,
            },
          );

          // Log the inconsistent state but don't fail the request
          // since the database update was successful
          logger.warn(
            'Database updated but storage upload failed - inconsistent state',
            {
              recordId,
              storageDocumentId,
              databaseVersion: updateResult.record.version,
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
        newFileName: fileUploaded ? originalname : undefined,
        updatedFields: Object.keys(updatedData),
        version: updateResult.record?.version,
        requestId: req.context?.requestId,
      });

      // Return the updated record
      res.status(200).json({
        message: fileUploaded
          ? 'Record updated with new file version'
          : 'Record updated successfully',
        record: updateResult.record,
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
      const connectors = req.query.connectors
        ? String(req.query.connectors).split(',')
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

      // Validate pagination parameters
      if (page < 1) {
        throw new BadRequestError('Page must be greater than 0');
      }
      if (limit < 1 || limit > 100) {
        throw new BadRequestError('Limit must be between 1 and 100');
      }

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
        'KB records not found',
        'Failed to get KB records',
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
      const connectors = req.query.connectors
        ? String(req.query.connectors).split(',')
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

      // Validate pagination parameters
      if (page < 1) {
        throw new BadRequestError('Page must be greater than 0');
      }
      if (limit < 1 || limit > 100) {
        throw new BadRequestError('Limit must be between 1 and 100');
      }

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
        'KB records not found',
        'Failed to get KB records',
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

      logger.info('Getting all records for user', {
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
      logger.info('All records retrieved successfully', {
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
        'Record not found',
        'Failed to get record',
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

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      // Call the Python service to get record
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/records/${recordId}/reindex`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        response,
        res,
        'Record not found',
        'Failed to reindex record',
      );

      // Log successful retrieval
      logger.info('Record reindexed successfully');
    } catch (error: any) {
      logger.error('Error reindexing record', {
        recordId: req.params.recordId,
        error,
      });
      const handleError = handleBackendError(error, 'reindex record');
      next(handleError);
      return; // Added return statement
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
        'Record not found',
        'Failed to delete record',
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
        `Creating ${role} permissions for ${userIds.length} users and ${teamIds.length} teams on KB ${kbId}`,
        {
          userIds:
            userIds.length > 5
              ? `${userIds.slice(0, 5).join(', ')} and ${userIds.length - 5} more`
              : userIds.join(', '),
          teamIds:
            teamIds.length > 5
              ? `${teamIds.slice(0, 5).join(', ')} and ${teamIds.length - 5} more`
              : teamIds.join(', '),
          role,
        },
      );

      try {
        const response = await executeConnectorCommand(
          `${appConfig.connectorBackend}/api/v1/kb/${kbId}/permissions`,
          HttpMethod.POST,
          req.headers as Record<string, string>,
          {
            userIds: userIds,
            teamIds: teamIds,
            role: role,
          },
        );

        if (response.statusCode !== 200) {
          throw new InternalServerError('Failed to create permissions');
        }

        const permissionResult = response.data as any;

        logger.info('Permissions created successfully', {
          kbId,
          grantedCount: permissionResult.grantedCount,
          updatedCount: permissionResult.updatedCount,
          role,
        });

        res.status(201).json({
          kbId: kbId,
          permissionResult,
        });
      } catch (pythonServiceError: any) {
        logger.error('Error calling Python service for permission creation', {
          kbId,
          error: pythonServiceError.message,
          response: pythonServiceError.response?.data,
        });

        // Handle different error types from Python service
        if (pythonServiceError.response?.status === 403) {
          throw new ForbiddenError(
            'Permission denied - only KB owners can grant permissions',
          );
        } else if (pythonServiceError.response?.status === 404) {
          const errorData = pythonServiceError.response?.data;
          if (errorData?.reason?.includes('Users not found')) {
            throw new NotFoundError(errorData.reason);
          } else {
            throw new NotFoundError('Knowledge base not found');
          }
        } else if (pythonServiceError.response?.status === 400) {
          throw new BadRequestError(
            pythonServiceError.response?.data?.reason ||
              'Invalid permission data',
          );
        } else {
          throw new InternalServerError(
            `Failed to create permissions: ${pythonServiceError.message}`,
          );
        }
      }
    } catch (error: any) {
      logger.error('Error Creating permissions for knowledge base', {
        error: error.message,
        kbId: req.params.kbId,
        status: error.response?.status,
        data: error.response?.data,
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

      try {
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
          throw new InternalServerError('Failed to update permission');
        }

        const updateResult = response.data as any;

        logger.info('Permission updated successfully', {
          kbId,
          userIds: updateResult.userIds,
          teamIds: updateResult.teamIds,
          newRole: updateResult.newRole,
        });

        res.status(200).json({
          kbId: kbId,
          userIds: updateResult.userIds,
          teamIds: updateResult.teamIds,
          newRole: updateResult.newRole,
        });
      } catch (pythonServiceError: any) {
        logger.error('Error calling Python service for permission update', {
          kbId,
          userIds: req.body.userIds,
          teamIds: req.body.teamIds,
          error: pythonServiceError.message,
          response: pythonServiceError.response?.data,
        });

        if (pythonServiceError.response?.status === 403) {
          throw new ForbiddenError(
            'Permission denied - only KB owners can update permissions',
          );
        } else if (pythonServiceError.response?.status === 404) {
          throw new NotFoundError(
            'User permission not found on this knowledge base',
          );
        } else if (pythonServiceError.response?.status === 400) {
          throw new BadRequestError(
            pythonServiceError.response?.data?.reason ||
              'Invalid permission update data',
          );
        } else {
          throw new InternalServerError(
            `Failed to update permission: ${pythonServiceError.message}`,
          );
        }
      }
    } catch (error: any) {
      logger.error('Error updating KB permission', {
        kbId: req.params.kbId,
        userIds: req.body.userIds,
        teamIds: req.body.teamIds,
        error: error.message,
        requestId: req.context?.requestId,
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

      try {
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
          throw new InternalServerError('Failed to remove permission');
        }

        const removeResult = response.data as any;

        logger.info('Permission removed successfully', {
          kbId,
          userIds: removeResult.userIds,
          teamIds: removeResult.teamIds,
        });

        res.status(200).json({
          kbId: kbId,
          userIds: removeResult.userIds,
          teamIds: removeResult.teamIds,
        });
      } catch (pythonServiceError: any) {
        logger.error('Error calling Python service for permission removal', {
          kbId,
          userIds: req.body.userIds,
          teamIds: req.body.teamIds,
          error: pythonServiceError.message,
          response: pythonServiceError.response?.data,
        });

        if (pythonServiceError.response?.status === 403) {
          throw new ForbiddenError(
            'Permission denied - only KB owners can remove permissions',
          );
        } else if (pythonServiceError.response?.status === 404) {
          throw new NotFoundError(
            'User permission not found on this knowledge base',
          );
        } else if (pythonServiceError.response?.status === 400) {
          throw new BadRequestError(
            pythonServiceError.response?.data?.reason ||
              'Cannot remove permission',
          );
        } else {
          throw new InternalServerError(
            `Failed to remove permission: ${pythonServiceError.message}`,
          );
        }
      }
    } catch (error: any) {
      logger.error('Error removing KB permission', {
        kbId: req.params.kbId,
        userIds: req.body.userIds,
        teamIds: req.body.teamIds,
        error: error.message,
        requesterId: req.user?.userId,
        requestId: req.context?.requestId,
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

      try {
        const response = await executeConnectorCommand(
          `${appConfig.connectorBackend}/api/v1/kb/${kbId}/permissions`,
          HttpMethod.GET,
          req.headers as Record<string, string>,
        );

        if (response.statusCode !== 200) {
          throw new InternalServerError('Failed to list permissions');
        }

        const listResult = response.data as any;

        logger.info('Permissions listed successfully', {
          kbId,
          totalCount: listResult.totalCount,
        });

        res.status(200).json({
          kbId: kbId,
          permissions: listResult.permissions,
          totalCount: listResult.totalCount,
        });
      } catch (pythonServiceError: any) {
        logger.error('Error calling Python service for permission listing', {
          kbId,
          error: pythonServiceError.message,
          response: pythonServiceError.response?.data,
        });

        if (pythonServiceError.response?.status === 403) {
          throw new ForbiddenError(
            'Permission denied - you do not have access to this knowledge base',
          );
        } else if (pythonServiceError.response?.status === 404) {
          throw new NotFoundError('Knowledge base not found');
        } else {
          throw new InternalServerError(
            `Failed to list permissions: ${pythonServiceError.message}`,
          );
        }
      }
    } catch (error: any) {
      logger.error('Error listing KB permissions', {
        kbId: req.params.kbId,
        error: error.message,
        requesterId: req.user?.userId,
        requestId: req.context?.requestId,
      });
      next(error);
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

      try {
        // Call the Python service to get record
        const response = await axios.get(
          `${appConfig.connectorBackend}/api/v1/stats`,
          {
            params: {
              org_id: orgId,
              connector: req.params.connector,
            },
          },
        );

        if (response.status !== 200) {
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
            logger.error('Failed to send stream error response to client', { error: e });
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

export const reindexAllRecords =
  (recordRelationService: RecordRelationService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const app = req.body.app;
      if (!userId || !orgId) {
        throw new BadRequestError('User not authenticated');
      }

      await validateActiveConnector(
        app,
        appConfig,
        req.headers as Record<string, string>,
      );

      const reindexPayload = {
        userId,
        orgId,
        app: normalizeAppName(app),
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
  (recordRelationService: RecordRelationService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const connectorName = req.body.connectorName;
      if (!userId || !orgId) {
        throw new BadRequestError('User not authenticated');
      }

      await validateActiveConnector(
        connectorName,
        appConfig,
        req.headers as Record<string, string>,
      );

      const resyncConnectorPayload = {
        userId,
        orgId,
        connectorName: normalizeAppName(connectorName),
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
