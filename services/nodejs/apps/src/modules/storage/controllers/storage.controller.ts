import mongoose from 'mongoose';
import { inject, injectable } from 'inversify';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import { Response, NextFunction } from 'express';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { endpoint, storageEtcdPaths } from '../constants/constants';
import {
  AzureBlobStorageConfig,
  LocalStorageConfig,
  S3StorageConfig,
} from '../config/storage.config';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerServiceCommand } from '../../../libs/commands/configuration_manager/cm.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { StorageServiceAdapter } from '../adapter/base-storage.adapter';
import {
  BadRequestError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  Document,
  FilePayload,
  StorageServiceResponse,
  StorageVendor,
} from '../types/storage.service.types';
import { StorageService } from '../storage.service';
import {
  DocumentInfoResponse,
  extractOrgId,
  extractUserId,
  getBaseUrl,
  getDocumentInfo,
  getStorageVendor,
  hasExtension,
  isValidStorageVendor,
  serveFileFromLocalStorage,
} from '../utils/utils';
import { UploadDocumentService } from './storage.upload.service';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import { DocumentModel } from '../schema/document.schema';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { getMimeType } from '../mimetypes/mimetypes';
import path from 'path';
import { ErrorMetadata } from '../../../libs/errors/base.error';

// TODO: Remove these globals
let storageConfig:
  | AzureBlobStorageConfig
  | LocalStorageConfig
  | S3StorageConfig
  | null = null;

@injectable()
export class StorageController {
  constructor(
    @inject('StorageConfig') private config: DefaultStorageConfig,
    private logger: Logger,
    @inject('KeyValueStoreService')
    private keyValueStoreService: KeyValueStoreService,
  ) {}

  async getStorageConfig(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    keyValueStoreService: KeyValueStoreService,
    defaultConfig: DefaultStorageConfig,
  ) {
    if (storageConfig != null) {
      return storageConfig;
    }

    const url = (await keyValueStoreService.get<string>(endpoint)) || '{}';
    let storageConfigRoute;
    if ('user' in req && req.user && 'userId' in req.user) {
      storageConfigRoute = 'api/v1/configurationManager/storageConfig';
    } else {
      storageConfigRoute = 'api/v1/configurationManager/internal/storageConfig';
    }
    const cmUrl = JSON.parse(url).cm.endpoint || defaultConfig.endpoint;

    const token = req.headers.authorization?.split(' ')[1];
    const configurationManagerServiceCommand =
      new ConfigurationManagerServiceCommand({
        uri: `${cmUrl}/${storageConfigRoute}`,
        method: HttpMethod.GET,
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
      });
    storageConfig = (await configurationManagerServiceCommand.execute()).data;
    return storageConfig;
  }
  async cloneDocument(
    document: mongoose.Document<unknown, {}, DocumentModel> & DocumentModel,
    buffer: Buffer,
    newDocumentFilePath: string,
    next: NextFunction,
    adapter: StorageServiceAdapter,
  ): Promise<StorageServiceResponse<string> | undefined> {
    try {
      const mimetype = getMimeType(document.extension);
      const cloneFilePayload: FilePayload = {
        buffer: buffer,
        mimeType: mimetype,
        documentPath: newDocumentFilePath,
        isVersioned: document.isVersionedFile,
      };
      return await adapter.uploadDocumentToStorageService(cloneFilePayload);
    } catch (error) {
      next(error);
      return undefined;
    }
  }

  async compareDocuments(
    document: DocumentModel,
    version1: number | undefined,
    version2: number | undefined,
    adapter: StorageServiceAdapter,
  ): Promise<boolean> {
    if (!document) {
      return false;
    }
    const response1 = await adapter.getBufferFromStorageService(
      document,
      version1,
    );
    const response2 = await adapter.getBufferFromStorageService(
      document,
      version2,
    );
    return (
      (response1.data as Buffer)?.equals(response2.data as Buffer) ?? false
    );
  }
  async getOrSetDefault(
    keyValueStoreService: KeyValueStoreService,
    key: string,
    defaultValue: string,
  ) {
    const value = await keyValueStoreService.get<string>(key);
    if (!value) {
      await keyValueStoreService.set(key, defaultValue);
      return defaultValue;
    }
    return value;
  }

  async watchStorageType(keyValueStoreService: KeyValueStoreService) {
    await keyValueStoreService.watchKey(storageEtcdPaths, () => {
      this.logger.debug('storage Config changed');
      storageConfig = null;
    });
  }

  async initializeStorageAdapter(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
  ): Promise<StorageServiceAdapter> {
    storageConfig = await this.getStorageConfig(
      req,
      this.keyValueStoreService,
      this.config,
    );
    if (!storageConfig) {
      throw new InternalServerError('Storage configuration not found');
    }
    const storageService = new StorageService(
      this.keyValueStoreService,
      storageConfig,
      this.config,
    );
    await storageService.initialize();
    const adapter = storageService.getAdapter();
    if (!adapter) {
      throw new InternalServerError('Storage service adapter not found');
    }
    return adapter;
  }

  async uploadDocument(
    req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const adapter = await this.initializeStorageAdapter(req);
      const storageConfig =
        (await this.keyValueStoreService.get<string>(storageEtcdPaths)) || '{}';
      const { storageType } = JSON.parse(storageConfig);
      if (!isValidStorageVendor(storageType ?? '')) {
        throw new BadRequestError(`Invalid storage type: ${storageType}`);
      }
      const uploadDocumentService = new UploadDocumentService(
        adapter,
        req.body.fileBuffer as FileBufferInfo,
        getStorageVendor(storageType ?? ''),
        this.keyValueStoreService,
        this.config,
      );
      return await uploadDocumentService.uploadDocument(req, res, next);
    } catch (error) {
      next(error);
    }
  }

  async createPlaceholderDocument(
    req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const {
        documentName,
        alternateDocumentName,
        documentPath,
        permissions,
        customMetadata,
        isVersionedFile,
        extension,
      } = req.body as Partial<Document>;

      const orgId = extractOrgId(req);
      const userId = extractUserId(req);
      if (!orgId) {
        throw new BadRequestError('OrgId not found in AuthToken');
      }
      if (hasExtension(documentName)) {
        throw new BadRequestError(
          'The name of the document cannot have extensions',
        );
      }

      if (documentName?.includes('/')) {
        throw new BadRequestError(
          'The name of the document cannot have forward slash',
        );
      }

      const storageConfig =
        (await this.keyValueStoreService.get<string>(storageEtcdPaths)) || '{}';
      const { storageType } = JSON.parse(storageConfig);
      const storageVendor = getStorageVendor(storageType ?? '');
      const documentInfo: Partial<Document> = {
        documentName,
        documentPath,
        alternateDocumentName,
        orgId: new mongoose.Types.ObjectId(orgId),
        isVersionedFile: isVersionedFile,
        initiatorUserId: userId ? new mongoose.Types.ObjectId(userId) : null,
        permissions: permissions,
        customMetadata,
        storageVendor: storageVendor,
        extension: `.${extension}`,
      };

      const savedDocument = await DocumentModel.create(documentInfo);
      res.status(200).json(savedDocument);
    } catch (error) {
      next(error);
    }
  }

  async getDocumentById(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { documentId } = req.params;

      if (!documentId) {
        throw new BadRequestError(' document id is not passed ');
      }
      const orgId = extractOrgId(req);
      const doc = await DocumentModel.findOne({
        _id: documentId,
        orgId: orgId,
      });

      if (!doc) {
        throw new NotFoundError('Document not found');
      }

      res.status(HTTP_STATUS.OK).json(doc);
    } catch (error) {
      next(error);
    }
  }

  async deleteDocumentById(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = extractOrgId(req);
      const userId = extractUserId(req);
      const { documentId } = req.params;
      const document = await DocumentModel.findOne({
        _id: documentId,
        orgId,
      });

      if (!document) {
        throw new NotFoundError('Document does not exist');
      }

      document.isDeleted = true;
      document.deletedByUserId = userId
        ? (new mongoose.Types.ObjectId(
            userId,
          ) as unknown as mongoose.Schema.Types.ObjectId)
        : undefined;

      await document.save();

      res.status(HTTP_STATUS.OK).json(document);
    } catch (error) {
      next(error);
    }
  }
  async downloadDocument(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const adapter = await this.initializeStorageAdapter(req);
      const version = req.query.version;
      const expirationTimeInSeconds = req.query.expirationTimeInSeconds;

      const docResult: DocumentInfoResponse | undefined = await getDocumentInfo(
        req,
        next,
      ); // Use the middleware to get docInfo
      if (!docResult) {
        throw new NotFoundError('Document does not exist');
      }

      const document = docResult.document;
      if (
        version &&
        Number(version) >= (document.versionHistory?.length ?? 0)
      ) {
        throw new BadRequestError("This version doesn't exist");
      }

      if (document.isVersionedFile === false && version !== undefined) {
        throw new BadRequestError('This is a non-versioned document');
      }

      if (
        version &&
        Number(version) >= (document.versionHistory?.length ?? 0)
      ) {
        throw new BadRequestError("This version doesn't exist");
      }
      const storageConfig =
        (await this.keyValueStoreService.get<string>(storageEtcdPaths)) || '{}';
      const { storageType } = JSON.parse(storageConfig);
      const storageVendorInETCD = storageType;

      // TODO: fix this usage
      if (document.storageVendor !== storageVendorInETCD) {
        throw new BadRequestError(
          'Storage vendor mismatch, provided storage vendor is not the same as the one used to create the document',
        );
      }

      const signedUrlResult = await adapter.getSignedUrl(
        document,
        version ? Number(version) : undefined,
        undefined, // fileName is not required for download TODO: fix this usage
        expirationTimeInSeconds ? Number(expirationTimeInSeconds) : 3600,
      );

      if (document.storageVendor === StorageVendor.Local) {
        serveFileFromLocalStorage(document, res);
      } else {
        res.status(200).json({ signedUrl: signedUrlResult.data });
      }
    } catch (error) {
      next(error);
    }
  }

  async getDocumentBuffer(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { version } = req.query;
      const docResult: DocumentInfoResponse | undefined = await getDocumentInfo(
        req,
        next,
      );
      if (!docResult) {
        throw new NotFoundError('Document does not exist');
      }

      const document = docResult.document;
      const lengthOfVersionHistory = document.versionHistory
        ? document.versionHistory.length
        : 0;

      if (version && Number(version) > lengthOfVersionHistory) {
        throw new BadRequestError("This version doesn't exist");
      }

      const adapter = await this.initializeStorageAdapter(req);
      const bufferResult = await adapter.getBufferFromStorageService(
        document,
        version ? Number(version) : undefined,
      );

      if (bufferResult.statusCode === HTTP_STATUS.OK) {
        res.status(HTTP_STATUS.OK).json(bufferResult.data);
      } else {
        res.status(HTTP_STATUS.INTERNAL_SERVER).json('Failed to get buffer');
      }
    } catch (error) {
      next(error);
    }
  }
  async createDocumentBuffer(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { buffer, size } = req.body.fileBuffer;

      const docResult: DocumentInfoResponse | undefined = await getDocumentInfo(
        req,
        next,
      );
      if (!docResult) {
        throw new NotFoundError('Document does not exist');
      }

      const document = docResult.document;

      const adapter = await this.initializeStorageAdapter(req);
      const uploadResult = await adapter.updateBuffer(buffer, document);

      if (uploadResult.statusCode === 200) {
        document.mutationCount = (document.mutationCount ?? 0) + 1;
        document.sizeInBytes = size;
        await document.save();
        res.status(200).json(uploadResult.data);
      } else {
        this.logger.error(`Failed to upload buffer: ${uploadResult.msg}`);
        throw new InternalServerError(
          `Failed to upload buffer: ${uploadResult.msg}`,
        );
      }
    } catch (error) {
      next(error);
    }
  }

  async uploadNextVersionDocument(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { buffer, originalname, size, mimetype } = req.body.fileBuffer;
      const currentVersionNote = req.body.currentVersionNote; // use this only when current was already modified when upload next version was clicked
      const nextVersionNote = req.body.nextVersionNote; // use this for next version note
      const userId = extractUserId(req);
      const docResult: DocumentInfoResponse | undefined = await getDocumentInfo(
        req,
        next,
      ); // Use the middleware to get docInfo

      if (!docResult) {
        throw new NotFoundError('Error fetching document from db');
      }

      const document = docResult.document;
      const storageType = document.storageVendor;

      if (document.isVersionedFile === false) {
        throw new BadRequestError('This document cannot be versioned');
      }

      const uploadedFileExtension = path.extname(originalname);

      if (document.extension !== uploadedFileExtension) {
        throw new ForbiddenError('Update buffer failed: File format mismatch');
      }

      await document.save();
      const adapter = await this.initializeStorageAdapter(req);
      const isDocumentChanged = !(await this.compareDocuments(
        document,
        undefined,
        document.versionHistory?.length
          ? document.versionHistory.length - 1
          : 0,
        adapter,
      ));

      if (isDocumentChanged === true) {
        const nextVersion = document.versionHistory?.length ?? 0;
        const newDocumentFilePath = `${document.documentPath}/versions/v${nextVersion}${document.extension}`;
        const bufferResponse = await adapter.getBufferFromStorageService(
          document,
          undefined, //passing version as undefined to get the buffer of current
        );

        if (bufferResponse.statusCode !== 200) {
          throw new InternalServerError(
            `Some error occurred while uploading next version: ${bufferResponse.msg}`,
          );
        }

        const response = await this.cloneDocument(
          document,
          bufferResponse.data as Buffer,
          newDocumentFilePath,
          next,
          adapter,
        );

        if (!response) {
          throw new InternalServerError('Failed to upload document');
        }

        if (response.statusCode !== 200) {
          throw new InternalServerError(
            response.data ?? 'Some error occurred while uploading next version',
          );
        }
        const storageConfig =
          (await this.keyValueStoreService.get<string>(storageEtcdPaths)) ||
          '{}';
        const { storageType } = JSON.parse(storageConfig);
        if (!isValidStorageVendor(storageType ?? '')) {
          throw new BadRequestError(`Invalid storage type: ${storageType}`);
        }

        document.versionHistory?.push({
          version: nextVersion,
          [`${storageType}`]: {
            url: response?.data,
          },
          mutationCount: document.mutationCount,
          size: document.sizeInBytes,
          extension: document.extension,
          note: currentVersionNote,
          initiatedByUserId: userId
            ? (new mongoose.Types.ObjectId(
                userId,
              ) as unknown as mongoose.Schema.Types.ObjectId)
            : undefined,
          createdAt: Date.now(),
        });

        await document.save();
      }

      const nextVersion = document.versionHistory?.length ?? 0;
      const newDocumentFilePath = `${document.documentPath}/versions/v${nextVersion}${document.extension}`;

      const nextVersionPayload: FilePayload = {
        buffer: buffer,
        mimeType: mimetype,
        documentPath: newDocumentFilePath,
        isVersioned: document.isVersionedFile,
      };

      const response = await adapter.uploadDocumentToStorageService(
        nextVersionPayload as FilePayload,
      );

      if (response.statusCode !== 200) {
        throw new InternalServerError(
          `some error occurred while uploading, try after sometime: ${response.msg}`,
        );
      }

      const currentFileResponse = await this.cloneDocument(
        document,
        buffer,
        `${document.documentPath}/current/${document.documentName}${document.extension}`,
        next,
        adapter,
      );

      if (currentFileResponse && currentFileResponse.statusCode !== 200) {
        throw new InternalServerError(
          currentFileResponse.data ??
            'Some error occurred while uploading next version',
        );
      }

      const fileExtension = path.extname(originalname);

      document.mutationCount = (document.mutationCount ?? 0) + 1;

      document.versionHistory?.push({
        version: nextVersion,
        [`${storageType}`]: {
          url: response?.data,
        },
        size: size,
        mutationCount: document.mutationCount,
        extension: fileExtension,
        note: nextVersionNote,
        initiatedByUserId: userId
          ? (new mongoose.Types.ObjectId(
              userId,
            ) as unknown as mongoose.Schema.Types.ObjectId)
          : undefined,
        createdAt: Date.now(),
      });

      document.sizeInBytes = size;

      await document.save();

      res.status(HTTP_STATUS.OK).json(document);
    } catch (error) {
      next(error);
    }
  }

  async rollBackToPreviousVersion(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { version, note } = req.body as { version: string; note: string };
      const userId = extractUserId(req);
      const docResult: DocumentInfoResponse | undefined = await getDocumentInfo(
        req,
        next,
      );
      if (!docResult) {
        throw new NotFoundError('Document Id does not exist');
      }

      const document = docResult.document;

      if (document.isVersionedFile) {
        throw new BadRequestError(
          'This is a non-versioned document, no previous exists',
        );
      }

      await document.save();

      const currentVersion = document.versionHistory
        ? document.versionHistory.length
        : 0;
      if (Number(version) >= currentVersion - 1) {
        throw new BadRequestError(
          `Rollback version greater than current: ${currentVersion - 1}`,
        );
      }
      const adapter = await this.initializeStorageAdapter(req);
      const bufferResult = await adapter.getBufferFromStorageService(
        document,
        Number(version),
      );

      if (bufferResult.statusCode !== HTTP_STATUS.OK) {
        throw new InternalServerError(
          `Some error occurred while rollback: ${bufferResult.msg}`,
        );
      }

      const currentFileResponse = await this.cloneDocument(
        document,
        bufferResult.data as Buffer,
        `${document.documentPath}/current/${document.documentName}${document.extension}`,
        next,
        adapter,
      );

      if (!currentFileResponse) {
        throw new InternalServerError(
          'Some error occurred while cloning document',
        );
      }

      const nextVersion = document.versionHistory
        ? document.versionHistory.length
        : 0;
      const newDocumentFilePath = `${document.documentPath}/versions/v${nextVersion}${document.extension}`;
      const response = await this.cloneDocument(
        document,
        bufferResult.data as Buffer,
        newDocumentFilePath,
        next,
        adapter,
      );

      if (!response) {
        throw new InternalServerError(
          'Some error occurred while cloning document',
        );
      }

      const storageConfig =
        (await this.keyValueStoreService.get<string>(storageEtcdPaths)) || '{}';
      const { storageType } = JSON.parse(storageConfig);
      if (!isValidStorageVendor(storageType ?? '')) {
        throw new BadRequestError(`Invalid storage type: ${storageType}`);
      }
      document.mutationCount = (document.mutationCount ?? 0) + 1;
      document.versionHistory = document.versionHistory ?? [];
      document.versionHistory.push({
        version: nextVersion,
        [`${storageType}`]: {
          url: response?.data,
        },
        mutationCount: document.mutationCount,
        extension: document.extension,
        note: note,
        size: document.versionHistory[Number(version)]?.size,
        initiatedByUserId: userId
          ? (new mongoose.Types.ObjectId(
              userId,
            ) as unknown as mongoose.Schema.Types.ObjectId)
          : undefined,
        createdAt: Date.now(),
      });

      document.sizeInBytes = document.versionHistory[Number(version)]?.size;

      await document.save();

      res.status(HTTP_STATUS.OK).json(document);
    } catch (error) {
      next(error);
    }
  }

  async uploadDirectDocument(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { documentId } = req.params;

      const document = await DocumentModel.findOne({
        _id: documentId,
        orgId: req.user?.orgId,
      });

      if (!document || !document.documentPath) {
        throw new NotFoundError('Document / Document Path does not exist');
      }
      const adapter = await this.initializeStorageAdapter(req);
      const presignedUrlResponse =
        await adapter.generatePresignedUrlForDirectUpload(
          document.documentPath,
        );

      if (presignedUrlResponse.statusCode !== 200) {
        this.logger.error(
          'Error generating presigned URL for part:',
          presignedUrlResponse.data?.url,
        );
        const errorMetadata: ErrorMetadata = {
          statusCode: presignedUrlResponse.statusCode,
          requestedUrl: presignedUrlResponse.data?.url,
          errorMessage: presignedUrlResponse.msg,
          timestamp: new Date().toISOString(),
        };
        throw new InternalServerError(
          'Error generating presigned URL for part:',
          errorMetadata,
        );
      }

      const url = presignedUrlResponse.data?.url;
      if (!url) {
        throw new InternalServerError('Failed to generate presigned URL');
      }

      const storageVendor = document.storageVendor;
      if (!isValidStorageVendor(storageVendor ?? '')) {
        throw new BadRequestError(`Invalid storage type: ${storageVendor}`);
      }

      if (storageVendor === StorageVendor.S3) {
        document.s3 = {
          url: getBaseUrl(url) as string,
        };
      } else if (storageVendor === StorageVendor.AzureBlob) {
        document.azureBlob = {
          url: getBaseUrl(url) as string,
        };
      }

      await document.save();
      res.status(HTTP_STATUS.OK).json({
        signedUrl: presignedUrlResponse.data?.url,
        documentId: document._id,
      });
    } catch (error) {
      next(error);
    }
  }

  async documentDiffChecker(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = extractOrgId(req);
      if (!orgId) {
        throw new NotFoundError('Organization ID not found');
      }
      // Fetch document info
      const docResult: DocumentInfoResponse | undefined = await getDocumentInfo(
        req,
        next,
      );

      if (!docResult) {
        throw new NotFoundError('Document does not exist');
      }

      const document = docResult.document;
      const adapter = await this.initializeStorageAdapter(req);
      const isDocumentChanged = !(await this.compareDocuments(
        document,
        undefined,
        document.versionHistory?.length
          ? document.versionHistory.length - 1
          : 0,
        adapter,
      ));

      if (isDocumentChanged === true) {
        res.status(HTTP_STATUS.OK).json(true);
      } else if (isDocumentChanged === false) {
        res.status(HTTP_STATUS.OK).json(false);
      } else {
        throw new InternalServerError(
          'Some error occurred while comparing documents',
        );
      }
    } catch (error) {
      next(error);
    }
  }
}
