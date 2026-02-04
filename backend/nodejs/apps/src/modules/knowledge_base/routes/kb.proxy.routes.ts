import { Router, Request, Response, NextFunction, RequestHandler } from 'express';
import { Container } from 'inversify';
import axios, { AxiosError } from 'axios';
import { v4 as uuidv4 } from 'uuid';
// eslint-disable-next-line @typescript-eslint/no-var-requires
const FormData = require('form-data');
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { KB_UPLOAD_LIMITS } from '../constants/kb.constants';
import { getPlatformSettingsFromStore } from '../../configuration_manager/utils/util';
import { FileProcessorService } from '../../../libs/middlewares/file_processor/fp.service';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import { extensionToMimeType, getMimeType } from '../../storage/mimetypes/mimetypes';
import { INDEXING_STATUS, ORIGIN_TYPE, RECORD_TYPE } from '../constants/record.constants';
import { endpoint } from '../../storage/constants/constants';

// Helper to get filename without extension
const getFilenameWithoutExtension = (filename: string): string => {
  const lastDotIndex = filename.lastIndexOf('.');
  return lastDotIndex > 0 ? filename.substring(0, lastDotIndex) : filename;
};

const logger = Logger.getInstance({
  service: 'KnowledgeBaseProxyRoutes',
});

/**
 * Creates a proxy router for Knowledge Base routes when using Neo4j.
 * This forwards requests to the Python connector service which has
 * the proper graph provider abstraction for both ArangoDB and Neo4j.
 */
export function createKnowledgeBaseProxyRouter(container: Container): Router {
  const router = Router();
  const appConfig = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const connectorBackend = appConfig.connectorBackend;

  // Helper to forward requests to Python connector service
  const proxyRequest = async (
    req: AuthenticatedUserRequest,
    res: Response,
    targetPath: string,
  ) => {
    try {
      const url = `${connectorBackend}${targetPath}`;
      
      // Forward the request with auth headers from authenticated user
      const response = await axios({
        method: req.method as any,
        url,
        params: req.query,
        data: req.body,
        headers: {
          'Content-Type': 'application/json',
          'Authorization': req.headers.authorization || '',
          'x-org-id': req.user?.orgId || '',
          'x-user-id': req.user?.userId || '',
        },
        validateStatus: () => true, // Don't throw on non-2xx
      });

      res.status(response.status).json(response.data);
    } catch (error) {
      const axiosError = error as AxiosError;
      logger.error(`Proxy request failed: ${axiosError.message}`);
      res.status(502).json({
        error: 'Bad Gateway',
        message: 'Failed to proxy request to connector service',
      });
    }
  };

  // Knowledge Hub nodes endpoint
  router.get(
    '/knowledge-hub/nodes',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, '/api/v2/knowledge-hub/nodes');
    },
  );

  // Knowledge Hub browse by parent type and ID (e.g., /nodes/app/{uuid}, /nodes/kb/{uuid})
  router.get(
    '/knowledge-hub/nodes/:parentType/:parentId',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(
        req,
        res,
        `/api/v2/knowledge-hub/nodes/${req.params.parentType}/${req.params.parentId}`,
      );
    },
  );

  // Connector stats endpoint
  router.get(
    '/stats/:connectorId',
    authMiddleware.authenticate,
    async (req: AuthenticatedUserRequest, res: Response) => {
      const orgId = req.user?.orgId;
      await proxyRequest(
        req,
        res,
        `/api/v1/stats?org_id=${orgId}&connector_id=${req.params.connectorId}`,
      );
    },
  );

  // ==================== KB CRUD Routes (proxy to /api/v1/kb) ====================

  // Upload limits endpoint - returns platform upload constraints (no DB needed)
  const keyValueStoreService = container.get<KeyValueStoreService>('KeyValueStoreService');
  
  const resolveMaxUploadSize = async (): Promise<number> => {
    try {
      const settings = await getPlatformSettingsFromStore(keyValueStoreService);
      return settings.fileUploadMaxSizeBytes;
    } catch (_e) {
      return KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes;
    }
  };

  router.get(
    '/limits',
    authMiddleware.authenticate,
    async (_req: Request, res: Response) => {
      try {
        res.status(200).json({
          maxFilesPerRequest: KB_UPLOAD_LIMITS.maxFilesPerRequest,
          maxFileSizeBytes: await resolveMaxUploadSize(),
        });
      } catch (error) {
        logger.error('Error getting limits', { error });
        res.status(500).json({ error: 'Failed to get upload limits' });
      }
    },
  );

  // All records endpoint - MUST be before /:kbId to avoid matching "records" as kbId
  router.get(
    '/records',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, '/api/v1/kb/records');
    },
  );

  // ==================== Record Routes (by record ID) ====================

  // Get a specific record by ID
  router.get(
    '/record/:recordId',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/records/${req.params.recordId}`);
    },
  );

  // Update a record (uses kb_router endpoint)
  router.put(
    '/record/:recordId',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/kb/record/${req.params.recordId}`);
    },
  );

  // Delete a record by ID
  router.delete(
    '/record/:recordId',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/records/${req.params.recordId}`);
    },
  );

  // Stream record (binary data - needs special handling, matches getRecordBuffer in kb.routes.ts)
  router.get(
    '/stream/record/:recordId',
    authMiddleware.authenticate,
    async (req: AuthenticatedUserRequest, res: Response) => {
      try {
        const { recordId } = req.params;
        const { convertTo } = req.query as { convertTo?: string };

        // Build query params (supports file conversion)
        const queryParams = new URLSearchParams();
        if (convertTo) {
          logger.info('Converting file to', { convertTo });
          queryParams.append('convertTo', convertTo);
        }

        const url = `${connectorBackend}/api/v1/stream/record/${recordId}?${queryParams.toString()}`;
        
        // Request as stream to handle binary data properly
        const response = await axios.get(url, {
          responseType: 'stream',
          headers: {
            'Authorization': req.headers.authorization || '',
            'x-org-id': req.user?.orgId || '',
            'x-user-id': req.user?.userId || '',
            'Content-Type': 'application/json',
          },
        });

        // Set appropriate headers from the Python response
        if (response.headers['content-type']) {
          res.set('Content-Type', response.headers['content-type']);
        }
        if (response.headers['content-disposition']) {
          res.set('Content-Disposition', response.headers['content-disposition']);
        }

        // Pipe the streaming response directly to the client
        response.data.pipe(res);

        // Handle any errors in the stream
        response.data.on('error', (error: Error) => {
          logger.error('Stream error:', { error: error.message });
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
        logger.error('Error fetching record buffer:', { error: error.message });
        if (!res.headersSent) {
          if (error.response) {
            // Forward status code and error from Python backend
            res.status(error.response.status).json({
              error: error.response.data || 'Error from connector backend',
            });
          } else {
            res.status(500).json({ error: 'Failed to retrieve record data' });
          }
        }
      }
    },
  );

  // Reindex a record
  router.post(
    '/reindex/record/:recordId',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/records/${req.params.recordId}/reindex`);
    },
  );

  // Move a record (requires kbId in the path)
  router.put(
    '/:kbId/record/:recordId/move',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(
        req,
        res,
        `/api/v1/kb/${req.params.kbId}/record/${req.params.recordId}/move`,
      );
    },
  );

  // List knowledge bases
  router.get(
    '/',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, '/api/v1/kb/');
    },
  );

  // Create knowledge base
  router.post(
    '/',
    authMiddleware.authenticate,
    async (req: AuthenticatedUserRequest, res: Response) => {
      try {
        const { kbName } = req.body;
        
        // Transform field name to match Python API expectations
        const url = `${connectorBackend}/api/v1/kb/`;
        const response = await axios({
          method: 'POST',
          url,
          data: { name: kbName },
          headers: {
            'Content-Type': 'application/json',
            'Authorization': req.headers.authorization || '',
            'x-org-id': req.user?.orgId || '',
            'x-user-id': req.user?.userId || '',
          },
          validateStatus: () => true,
        });
        
        res.status(response.status).json(response.data);
      } catch (error) {
        const axiosError = error as AxiosError;
        logger.error(`Create KB proxy request failed: ${axiosError.message}`);
        res.status(502).json({
          error: 'Bad Gateway',
          message: 'Failed to proxy request to connector service',
        });
      }
    },
  );

  // Get specific knowledge base
  router.get(
    '/:kbId',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/kb/${req.params.kbId}`);
    },
  );

  // Update knowledge base
  router.put(
    '/:kbId',
    authMiddleware.authenticate,
    async (req: AuthenticatedUserRequest, res: Response) => {
      try {
        const { kbName } = req.body;
        
        // Transform field name to match Python API expectations
        const url = `${connectorBackend}/api/v1/kb/${req.params.kbId}`;
        const response = await axios({
          method: 'PUT',
          url,
          data: { groupName: kbName },
          headers: {
            'Content-Type': 'application/json',
            'Authorization': req.headers.authorization || '',
            'x-org-id': req.user?.orgId || '',
            'x-user-id': req.user?.userId || '',
          },
          validateStatus: () => true,
        });
        
        res.status(response.status).json(response.data);
      } catch (error) {
        const axiosError = error as AxiosError;
        logger.error(`Update KB proxy request failed: ${axiosError.message}`);
        res.status(502).json({
          error: 'Bad Gateway',
          message: 'Failed to proxy request to connector service',
        });
      }
    },
  );

  // Delete knowledge base
  router.delete(
    '/:kbId',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/kb/${req.params.kbId}`);
    },
  );

  // Get KB records
  router.get(
    '/:kbId/records',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/kb/${req.params.kbId}/records`);
    },
  );

  // Create records in KB
  router.post(
    '/:kbId/records',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/kb/${req.params.kbId}/records`);
    },
  );

  // Get KB children
  router.get(
    '/:kbId/children',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/kb/${req.params.kbId}/children`);
    },
  );

  // KB permissions
  router.get(
    '/:kbId/permissions',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/kb/${req.params.kbId}/permissions`);
    },
  );

  router.post(
    '/:kbId/permissions',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/kb/${req.params.kbId}/permissions`);
    },
  );

  // Folder operations
  router.post(
    '/:kbId/folder',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(req, res, `/api/v1/kb/${req.params.kbId}/folder`);
    },
  );

  router.get(
    '/:kbId/folder/:folderId/children',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(
        req,
        res,
        `/api/v1/kb/${req.params.kbId}/folder/${req.params.folderId}/children`,
      );
    },
  );

  router.post(
    '/:kbId/folder/:folderId/subfolder',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(
        req,
        res,
        `/api/v1/kb/${req.params.kbId}/folder/${req.params.folderId}/subfolder`,
      );
    },
  );

  router.post(
    '/:kbId/folder/:folderId/records',
    authMiddleware.authenticate,
    async (req: Request, res: Response) => {
      await proxyRequest(
        req,
        res,
        `/api/v1/kb/${req.params.kbId}/folder/${req.params.folderId}/records`,
      );
    },
  );

  // ==================== Upload Routes ====================

  // Create per-request dynamic buffer upload processor (same as kb.routes.ts)
  const createDynamicBufferUpload = (opts: {
    fieldName: string;
    allowedMimeTypes: string[];
    maxFilesAllowed: number;
    isMultipleFilesAllowed: boolean;
    strictFileUpload: boolean;
  }): RequestHandler[] => {
    const handler: RequestHandler = async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const maxFileSize = await resolveMaxUploadSize();
        const service = new FileProcessorService({
          fieldName: opts.fieldName,
          allowedMimeTypes: opts.allowedMimeTypes,
          maxFilesAllowed: opts.maxFilesAllowed,
          isMultipleFilesAllowed: opts.isMultipleFilesAllowed,
          processingType: FileProcessingType.BUFFER,
          maxFileSize,
          strictFileUpload: opts.strictFileUpload,
        });
        const upload = service.upload();
        upload(req, res, (err: any) => {
          if (err) return next(err);
          const process = service.processFiles();
          process(req, res, next);
        });
      } catch (_e) {
        logger.error('Error creating dynamic buffer upload', { error: _e });
        next(_e);
      }
    };
    return [handler];
  };

  // Helper to upload file to storage service
  const uploadToStorage = async (
    req: AuthenticatedUserRequest,
    file: FileBufferInfo,
    documentName: string,
    isVersionedFile: boolean,
  ): Promise<{ documentId: string; documentName: string }> => {
    const formData = new FormData();
    formData.append('file', file.buffer, {
      filename: file.originalname,
      contentType: file.mimetype,
    });

    const url = (await keyValueStoreService.get<string>(endpoint)) || '{}';
    const storageUrl = JSON.parse(url).storage?.endpoint || appConfig.storage.endpoint;

    formData.append(
      'documentPath',
      `PipesHub/KnowledgeBase/private/${req.user?.userId}`,
    );
    formData.append('isVersionedFile', isVersionedFile.toString());
    formData.append('documentName', getFilenameWithoutExtension(documentName));

    try {
      const response = await axios.post(
        `${storageUrl}/api/v1/document/upload`,
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
      if (error.response?.status === 308) {
        // Handle redirect - extract document info from headers
        const documentId = error.response.headers['x-document-id'];
        const documentName = error.response.headers['x-document-name'];
        const redirectUrl = error.response.headers.location;

        // Upload file in background
        axios({
          method: 'put',
          url: redirectUrl,
          data: file.buffer,
          headers: {
            'Content-Type': file.mimetype,
            'Content-Length': file.buffer.length,
          },
          maxContentLength: Infinity,
          maxBodyLength: Infinity,
        }).catch((err) => {
          logger.error('Background upload failed', { documentId, error: err.message });
        });

        return { documentId, documentName };
      }
      throw error;
    }
  };

  // Upload files to KB root
  router.post(
    '/:kbId/upload',
    authMiddleware.authenticate,
    ...createDynamicBufferUpload({
      fieldName: 'files',
      allowedMimeTypes: Object.values(extensionToMimeType),
      maxFilesAllowed: KB_UPLOAD_LIMITS.maxFilesPerRequest,
      isMultipleFilesAllowed: true,
      strictFileUpload: true,
    }),
    async (req: AuthenticatedUserRequest, res: Response) => {
      try {
        const fileBuffers: FileBufferInfo[] = req.body.fileBuffers || [];
        const userId = req.user?.userId;
        const orgId = req.user?.orgId;
        const { kbId } = req.params;
        const isVersioned = req.body?.isVersioned ?? true;

        if (!userId || !orgId) {
          res.status(401).json({ error: 'User not authenticated' });
          return;
        }

        if (!kbId || fileBuffers.length === 0) {
          res.status(400).json({ error: 'Knowledge Base ID and files are required' });
          return;
        }

        logger.info('Processing file upload to KB (Neo4j mode)', {
          totalFiles: fileBuffers.length,
          kbId,
          userId,
        });

        const currentTime = Date.now();
        const processedFiles = [];

        for (const file of fileBuffers) {
          const { originalname, mimetype, size, filePath, lastModified } = file;

          const fileName = filePath?.includes('/')
            ? filePath.split('/').pop() || originalname
            : filePath || originalname;

          const extension = fileName.includes('.')
            ? fileName.substring(fileName.lastIndexOf('.') + 1).toLowerCase()
            : null;

          const correctMimeType = (extension && getMimeType(extension)) || mimetype;
          const key = uuidv4();
          const webUrl = `/record/${key}`;

          const validLastModified =
            lastModified && !isNaN(lastModified) && lastModified > 0
              ? lastModified
              : currentTime;

          const connectorId = `knowledgeBase_${orgId}`;

          // Upload to storage
          const { documentId, documentName } = await uploadToStorage(
            req,
            file,
            fileName,
            isVersioned,
          );

          const record = {
            _key: key,
            orgId,
            recordName: documentName,
            externalRecordId: documentId,
            recordType: RECORD_TYPE.FILE,
            origin: ORIGIN_TYPE.UPLOAD,
            createdAtTimestamp: currentTime,
            updatedAtTimestamp: currentTime,
            sourceCreatedAtTimestamp: validLastModified,
            sourceLastModifiedTimestamp: validLastModified,
            isDeleted: false,
            isArchived: false,
            indexingStatus: INDEXING_STATUS.QUEUED,
            version: 1,
            webUrl,
            mimeType: correctMimeType,
            connectorId,
            sizeInBytes: size,
          };

          const fileRecord = {
            _key: key,
            orgId,
            name: documentName,
            isFile: true,
            extension,
            mimeType: correctMimeType,
            sizeInBytes: size,
            webUrl,
          };

          processedFiles.push({
            record,
            fileRecord,
            filePath: filePath || originalname,
            lastModified: validLastModified,
          });
        }

        logger.info('Files processed, sending to Python service', {
          count: processedFiles.length,
        });

        // Forward to Python service
        const response = await axios({
          method: 'POST',
          url: `${connectorBackend}/api/v1/kb/${kbId}/upload`,
          data: { files: processedFiles },
          headers: {
            'Content-Type': 'application/json',
            'Authorization': req.headers.authorization || '',
            'x-org-id': orgId,
            'x-user-id': userId,
          },
          validateStatus: () => true,
        });

        res.status(response.status).json(response.data);
      } catch (error: any) {
        logger.error('Upload failed', { error: error.message });
        res.status(500).json({ error: 'Upload failed', message: error.message });
      }
    },
  );

  // Upload files to folder
  router.post(
    '/:kbId/folder/:folderId/upload',
    authMiddleware.authenticate,
    ...createDynamicBufferUpload({
      fieldName: 'files',
      allowedMimeTypes: Object.values(extensionToMimeType),
      maxFilesAllowed: KB_UPLOAD_LIMITS.maxFilesPerRequest,
      isMultipleFilesAllowed: true,
      strictFileUpload: true,
    }),
    async (req: AuthenticatedUserRequest, res: Response) => {
      try {
        const fileBuffers: FileBufferInfo[] = req.body.fileBuffers || [];
        const userId = req.user?.userId;
        const orgId = req.user?.orgId;
        const { kbId, folderId } = req.params;
        const isVersioned = req.body?.isVersioned ?? true;

        if (!userId || !orgId) {
          res.status(401).json({ error: 'User not authenticated' });
          return;
        }

        if (!kbId || !folderId || fileBuffers.length === 0) {
          res.status(400).json({ error: 'Knowledge Base ID, folder ID, and files are required' });
          return;
        }

        logger.info('Processing file upload to folder (Neo4j mode)', {
          totalFiles: fileBuffers.length,
          kbId,
          folderId,
          userId,
        });

        const currentTime = Date.now();
        const processedFiles = [];

        for (const file of fileBuffers) {
          const { originalname, mimetype, size, filePath, lastModified } = file;

          const fileName = filePath?.includes('/')
            ? filePath.split('/').pop() || originalname
            : filePath || originalname;

          const extension = fileName.includes('.')
            ? fileName.substring(fileName.lastIndexOf('.') + 1).toLowerCase()
            : null;

          const correctMimeType = (extension && getMimeType(extension)) || mimetype;
          const key = uuidv4();
          const webUrl = `/record/${key}`;

          const validLastModified =
            lastModified && !isNaN(lastModified) && lastModified > 0
              ? lastModified
              : currentTime;

          const connectorId = `knowledgeBase_${orgId}`;

          // Upload to storage
          const { documentId, documentName } = await uploadToStorage(
            req,
            file,
            fileName,
            isVersioned,
          );

          const record = {
            _key: key,
            orgId,
            recordName: documentName,
            externalRecordId: documentId,
            recordType: RECORD_TYPE.FILE,
            origin: ORIGIN_TYPE.UPLOAD,
            createdAtTimestamp: currentTime,
            updatedAtTimestamp: currentTime,
            sourceCreatedAtTimestamp: validLastModified,
            sourceLastModifiedTimestamp: validLastModified,
            isDeleted: false,
            isArchived: false,
            indexingStatus: INDEXING_STATUS.QUEUED,
            version: 1,
            webUrl,
            mimeType: correctMimeType,
            connectorId,
            sizeInBytes: size,
          };

          const fileRecord = {
            _key: key,
            orgId,
            name: documentName,
            isFile: true,
            extension,
            mimeType: correctMimeType,
            sizeInBytes: size,
            webUrl,
          };

          processedFiles.push({
            record,
            fileRecord,
            filePath: filePath || originalname,
            lastModified: validLastModified,
          });
        }

        logger.info('Files processed, sending to Python service', {
          count: processedFiles.length,
        });

        // Forward to Python service
        const response = await axios({
          method: 'POST',
          url: `${connectorBackend}/api/v1/kb/${kbId}/folder/${folderId}/upload`,
          data: { files: processedFiles },
          headers: {
            'Content-Type': 'application/json',
            'Authorization': req.headers.authorization || '',
            'x-org-id': orgId,
            'x-user-id': userId,
          },
          validateStatus: () => true,
        });

        res.status(response.status).json(response.data);
      } catch (error: any) {
        logger.error('Upload to folder failed', { error: error.message });
        res.status(500).json({ error: 'Upload failed', message: error.message });
      }
    },
  );

  logger.info('Knowledge Base proxy routes initialized (forwarding to connector service)');

  return router;
}

