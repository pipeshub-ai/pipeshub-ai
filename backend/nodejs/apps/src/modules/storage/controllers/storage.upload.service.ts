import { NextFunction, Response } from 'express';
import mongoose from 'mongoose';
import path from 'path';
import { getMimeType } from '../mimetypes/mimetypes';
import {
  Document,
  FilePayload,
  StorageInfo,
  StorageServiceResponse,
  StorageVendor,
} from '../types/storage.service.types';
import { DocumentModel } from '../schema/document.schema';
import {
  BadRequestError,
  InternalServerError,
} from '../../../libs/errors/http.errors';
import { StorageServiceAdapter } from '../adapter/base-storage.adapter';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  parseBoolean,
  getExtension,
  createPlaceholderDocument,
  generatePresignedUrlForDirectUpload,
  getBaseUrl,
  isValidStorageVendor,
  extractOrgId,
  extractUserId,
  validateFileAndDocumentName,
} from '../utils/utils';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import {
  maxFileSizeForPipesHubService,
  endpoint,
} from '../constants/constants';
import { Logger } from '../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';

const logger = Logger.getInstance({
  service: 'storage.upload.service',
});

interface DocumentDetails {
  buffer: Buffer;
  mimeType: string;
  originalName: string;
  size: number;
}

export class UploadDocumentService {
  private readonly storageServiceWrapper: StorageServiceAdapter;
  private readonly storageVendor: StorageVendor;
  private readonly fileBuffer: FileBufferInfo;
  private readonly keyValueStoreService: KeyValueStoreService;
  private readonly defaultConfig: DefaultStorageConfig;
  constructor(
    storageServiceWrapper: StorageServiceAdapter,
    fileBuffer: FileBufferInfo,
    storageVendor: StorageVendor,
    keyValueStoreService: KeyValueStoreService,
    defaultConfig: DefaultStorageConfig,
  ) {
    this.storageServiceWrapper = storageServiceWrapper;
    this.storageVendor = storageVendor;
    this.fileBuffer = fileBuffer;
    this.keyValueStoreService = keyValueStoreService;
    this.defaultConfig = defaultConfig;
  }

  async uploadDocument(
    req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const { buffer, originalname, size } = this.fileBuffer;

    const extension = getExtension(originalname);
    if (extension === '') {
      throw new BadRequestError(
        `File "${originalname}" does not have a valid file extension.`,
      );
    }
    // Use direct upload api provided by storage vendors for files size > 10MB
    if (
      (size > maxFileSizeForPipesHubService &&
        this.storageVendor === StorageVendor.S3) ||
      (size > maxFileSizeForPipesHubService &&
        this.storageVendor === StorageVendor.AzureBlob)
    ) {
      const placeholderDocument = await createPlaceholderDocument(
        req,
        next,
        size,
        extension,
        originalname,
      );
      if (!placeholderDocument || !placeholderDocument.document) {
        throw new InternalServerError('Failed to create placeholder document');
      }

      logger.info('Generating presigned url for direct upload');
      const documentPath = `${placeholderDocument.document.documentPath}/${placeholderDocument.document._id}`;
      const storageURL = await generatePresignedUrlForDirectUpload(
        this.storageServiceWrapper,
        documentPath,
      );
      if (process.env.NODE_ENV == 'development') {
        logger.info('Presigned url generated for direct upload', {
          storageURL,
        });
      }

      // set location header to the s3URL
      if (storageURL) {
        res.setHeader('Location', storageURL);
        res.setHeader(
          'x-document-id',
          placeholderDocument.document._id as string,
        );
        res.setHeader(
          'x-document-name',
          placeholderDocument.document.documentName as string,
        );
        const baseUrl = getBaseUrl(storageURL);
        if (!baseUrl) {
          throw new InternalServerError('Failed to get base url');
        }
        if (this.storageVendor === StorageVendor.S3) {
          placeholderDocument.document.s3 = { url: baseUrl };
        } else if (this.storageVendor === StorageVendor.AzureBlob) {
          placeholderDocument.document.azureBlob = { url: baseUrl };
        }

        await placeholderDocument.document.save();
        res.status(HTTP_STATUS.PERMANENT_REDIRECT).json(placeholderDocument);
        return;
      }
      throw new InternalServerError(
        'Failed to generate presigned url for direct upload',
      );
    }

    // Validate file extension, MIME type, and document name constraints
    const { documentName } = req.body as Partial<Document>;
    validateFileAndDocumentName(extension, documentName, originalname);

    // Get MIME type after validation (it's guaranteed to be valid at this point)
    const mimeType = getMimeType(extension);

    if (originalname.includes('/') === true) {
      throw new BadRequestError(
        `File "${originalname}": The file name cannot contain a forward slash.`,
      );
    }

    return this.handleDocumentUpload(req, res, () => ({
      buffer,
      mimeType: mimeType,
      originalName: originalname,
      size,
    }));
  }

  async handleDocumentUpload(
    req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
    res: Response,
    getDocumentDetails: () => DocumentDetails,
  ): Promise<void> {
    const {
      documentName,
      alternateDocumentName,
      documentPath,
      permissions,
      customMetadata,
      isVersionedFile,
    } = req.body as Partial<Document>;
    const isVersioned = parseBoolean(isVersionedFile);

    const { buffer, mimeType, originalName, size } = getDocumentDetails();

    const fileExtension = path.extname(originalName);
    // Create document record
    const orgId = extractOrgId(req);
    const userId = extractUserId(req);
    const documentInfo: Partial<Document> = {
      documentName,
      alternateDocumentName,
      orgId: new mongoose.Types.ObjectId(orgId),
      isVersionedFile: isVersioned,
      initiatorUserId: userId ? new mongoose.Types.ObjectId(userId) : null,
      permissions,
      sizeInBytes: size,
      customMetadata,
      extension: fileExtension,
      createdAt: Date.now(),
      isDeleted: false,
      storageVendor: this.storageVendor,
    };

    const savedDocument = await DocumentModel.create(documentInfo);
    let rootPath = '';
    // path of the document in the storage service
    if (documentPath) {
      rootPath = `${orgId}/PipesHub/${documentPath}/${savedDocument._id}`;
    } else {
      rootPath = `${orgId}/PipesHub/${savedDocument._id}`;
    }

    const concatenatedPath =
      isVersioned === false
        ? `${rootPath}/${documentName}${fileExtension}`
        : `${rootPath}/current/${documentName}${fileExtension}`;

    const uploadResult =
      await this.storageServiceWrapper.uploadDocumentToStorageService({
        buffer,
        mimeType,
        documentPath: concatenatedPath,
        isVersioned,
      });

    if (uploadResult.statusCode === HTTP_STATUS.OK && uploadResult.data) {
      savedDocument.documentPath = rootPath;

      const storageTypeKey = this.storageVendor;
      let normalizedUrl = '';
      let localPath = '';
      // Type-safe storage assignment
      if (isValidStorageVendor(storageTypeKey)) {
        // TODO : Move this to the local storage provider
        if (storageTypeKey === StorageVendor.Local) {
          const url =
            (await this.keyValueStoreService.get<string>(endpoint)) || '{}';

          const storageServiceEndpoint =
            JSON.parse(url).storage.endpoint || this.defaultConfig.endpoint;
          localPath = uploadResult.data;
          // normalize the url to the local storage
          const baseUrl = uploadResult.data.replace(
            'file://',
            `${storageServiceEndpoint}/api/v1/document/${savedDocument._id}/download`,
          );
          // Remove everything after "download" if it exists
          normalizedUrl = baseUrl.split('/download')[0] + '/download';
          const storageInfo: StorageInfo = {
            url: normalizedUrl,
            localPath: localPath,
          };
          savedDocument[storageTypeKey] = storageInfo;
        } else {
          const storageInfo: StorageInfo = { url: uploadResult.data };
          savedDocument[storageTypeKey] = storageInfo;
        }
      } else {
        throw new InternalServerError(
          `Invalid storage type: ${storageTypeKey}`,
        );
      }

      if (isVersioned === false) {
        await savedDocument.save();
        res.status(200).json(savedDocument);
        return;
      }

      if (savedDocument.versionHistory?.length === 0) {
        const nextVersion = savedDocument.versionHistory.length;
        const newDocumentFilePath = `${rootPath}/versions/v${nextVersion}${fileExtension}`;

        const cloneResponse = await this.cloneDocument(
          savedDocument,
          buffer,
          newDocumentFilePath,
        );
        // normalize the url to the local storage
        if (storageTypeKey === StorageVendor.Local) {
          cloneResponse.data = normalizedUrl;
        }

        if (cloneResponse.statusCode === HTTP_STATUS.OK && cloneResponse.data) {
          savedDocument.versionHistory.push({
            version: nextVersion,
            [`${storageTypeKey}`]: {
              url: cloneResponse.data,
              localPath: localPath,
            },
            createdAt: Date.now(),
            size: savedDocument.sizeInBytes,
            extension: savedDocument.extension,
          });
        }
      }
      await savedDocument.save();
      res.status(200).json(savedDocument);
    }
  }

  /**
   * Clones a document by uploading its buffer to a new path
   * @param document - The source document to clone
   * @param buffer - The document's content buffer
   * @param newDocumentFilePath - The target path for the cloned document
   * @returns A promise resolving to the storage service response
   */
  private async cloneDocument(
    document: Document,
    buffer: Buffer,
    newDocumentFilePath: string,
  ): Promise<StorageServiceResponse<string>> {
    try {
      // Get mime type from document extension without the dot
      const extension = document.extension.replace('.', '');
      const mimeType = getMimeType(extension);

      if (!mimeType) {
        throw new BadRequestError('Invalid document extension');
      }

      const cloneFilePayload: FilePayload = {
        buffer,
        mimeType,
        documentPath: newDocumentFilePath,
        isVersioned: document.isVersionedFile,
      };

      const response =
        await this.storageServiceWrapper.uploadDocumentToStorageService(
          cloneFilePayload,
        );

      if (response.statusCode !== HTTP_STATUS.OK) {
        throw new InternalServerError(
          `Error in cloning document: ${response.msg}`,
        );
      }

      return response;
    } catch (error) {
      if (error instanceof Error) {
        throw new InternalServerError(
          `Failed to clone document: ${error.message}`,
        );
      }
      throw new InternalServerError('Failed to clone document');
    }
  }
}
