import { randomUUID } from 'crypto';
import { NextFunction, Response } from 'express';
import path from 'path';
import { getMimeType } from '../mimetypes/mimetypes';
import {
  Document,
  DocumentVersion,
  FilePayload,
  StorageInfo,
  StorageServiceResponse,
  StorageVendor,
} from '../types/storage.service.types';
import {
  BadRequestError,
  ConflictError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
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
  getCurrentFilePath,
  getDocumentRootPath,
  getFullDocumentPath,
  getVersionFilePath,
  isValidStorageVendor,
  extractOrgId,
  DocumentInfoResponse,
  extractUserId,
  normalizeExtension,
  requireId,
  validateFileAndDocumentName,
  writeToStorage,
} from '../utils/utils';
import {
  UPLOAD_LEASE_MS,
  claimUpload,
  createDocumentOnce,
  getIdempotencyKey,
  releaseUpload,
  requestFingerprint,
} from '../utils/idempotency';
import {
  getDocumentRepository,
  type DocumentPatch,
} from '../../../libs/repositories/document';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import {
  maxFileSizeForPipesHubService,
  STORAGE_WRITE_FAILED_MESSAGE,
} from '../constants/constants';
import { storedServiceEndpoint } from '../utils/service-endpoint';
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
      try {
        await this.startDirectUpload(
          req,
          res,
          placeholderDocument,
          originalname,
        );
      } catch (error) {
        // Nothing was uploaded yet, so the placeholder would only be a file-less entry.
        await this.removeUnstoredDocument(placeholderDocument.document._id);
        throw error;
      }
      return;
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

  /** Answers with a signed URL the client uploads to directly; the placeholder records where. */
  private async startDirectUpload(
    req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
    res: Response,
    placeholderDocument: DocumentInfoResponse,
    originalname: string,
  ): Promise<void> {
    logger.debug('Generating presigned url for direct upload');
    // Extract required fields to construct path matching regular upload structure
    const orgId = extractOrgId(req);
    const placeholderDoc = placeholderDocument.document;
    const documentId = placeholderDoc._id;
    const documentName = placeholderDoc.documentName;
    const isVersioned = parseBoolean(placeholderDoc.isVersionedFile);

    const strippedDocPath = placeholderDoc.documentPath
      ? placeholderDoc.documentPath.replace(/^.*?PipesHub\/?/, '')
      : undefined;
    const ext = normalizeExtension(path.extname(originalname));
    const rootPath = getDocumentRootPath(
      orgId ?? '',
      documentId,
      strippedDocPath,
    );
    const fullDocumentPath = getFullDocumentPath(orgId ?? '', strippedDocPath);
    const concatenatedPath = getCurrentFilePath(
      rootPath,
      documentName ?? '',
      ext,
      isVersioned,
    );

    let storageURL: string | undefined;
    try {
      storageURL = await generatePresignedUrlForDirectUpload(
        this.storageServiceWrapper,
        concatenatedPath,
      );
    } catch (error) {
      logger.error('Could not get a direct-upload URL from storage', {
        documentId: documentId,
        error: error instanceof Error ? error.message : String(error),
      });
      throw new ServiceUnavailableError(STORAGE_WRITE_FAILED_MESSAGE);
    }
    const baseUrl = storageURL ? getBaseUrl(storageURL) : undefined;
    if (!storageURL || !baseUrl) {
      logger.error('Storage returned no direct-upload URL', {
        documentId: documentId,
      });
      throw new ServiceUnavailableError(STORAGE_WRITE_FAILED_MESSAGE);
    }
    if (process.env.NODE_ENV == 'development') {
      // Never the URL itself. A presigned URL carries its own authorization,
      // so anyone who can read the log can perform the upload until it
      // expires — and dev logs get retained, exported and shared.
      logger.info('Presigned url generated for direct upload', {
        documentId,
      });
    }

    const patch: DocumentPatch = {
      documentPath: fullDocumentPath,
      awaitingDirectUpload: true,
    };
    if (this.storageVendor === StorageVendor.S3) {
      patch.s3 = { url: baseUrl };
    } else if (this.storageVendor === StorageVendor.AzureBlob) {
      patch.azureBlob = { url: baseUrl };
    }
    const saved = await getDocumentRepository().update(
      orgId,
      documentId,
      patch,
    );
    if (!saved) {
      throw new NotFoundError('Document does not exist');
    }

    res.setHeader('Location', storageURL);
    res.setHeader('x-document-id', documentId);
    res.setHeader('x-document-name', documentName as string);
    res.status(HTTP_STATUS.PERMANENT_REDIRECT).json({ document: saved });
  }

  /**
   * Drops a document whose file never reached storage. The vendor field is set
   * only once a file is stored, so a stored document is never removed here.
   */
  private async removeUnstoredDocument(
    documentId: unknown,
    documentPath?: string,
  ): Promise<void> {
    try {
      if (documentPath !== undefined) {
        // Keep the document when storage cannot say the path is empty: it may
        // describe a file that arrived after the request gave up.
        const stored =
          await this.storageServiceWrapper.objectExistsAtPath(documentPath);
        if (stored) {
          logger.warn(
            'A failed upload left a file in storage; keeping its document',
            { documentId: documentId },
          );
          return;
        }
      }
      await getDocumentRepository().removeUnstored(
        String(documentId),
        this.storageVendor,
      );
    } catch (error) {
      logger.warn('Could not remove a document whose upload failed', {
        documentId: documentId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
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
      orgId: requireId(orgId, 'organization'),
      isVersionedFile: isVersioned,
      initiatorUserId: userId ? requireId(userId, 'user') : null,
      permissions,
      sizeInBytes: size,
      customMetadata,
      extension: fileExtension,
      createdAt: Date.now(),
      isDeleted: false,
      storageVendor: this.storageVendor,
    };

    const idempotencyKey = getIdempotencyKey(req);
    // This attempt's claim on a keyed upload until the file is stored.
    const leaseToken = idempotencyKey === undefined ? undefined : randomUUID();
    const created = await createDocumentOnce(
      documentInfo,
      idempotencyKey === undefined
        ? undefined
        : {
            key: idempotencyKey,
            fingerprint: requestFingerprint(
              {
                documentName,
                documentPath,
                isVersionedFile: isVersioned,
                extension: fileExtension,
                customMetadata,
              },
              buffer,
            ),
          },
      {
        uploadLeaseToken: leaseToken,
        uploadLeaseExpiresAt: Date.now() + UPLOAD_LEASE_MS,
      },
    );
    let savedDocument = created.document;
    if (created.replayed && leaseToken !== undefined) {
      // A retry of an upload that already finished gets its result.
      if (savedDocument[this.storageVendor]) {
        res.status(200).json(savedDocument);
        return;
      }
      // Unfinished: take it over, unless another attempt still holds it.
      const claimed = await claimUpload(
        savedDocument,
        this.storageVendor,
        leaseToken,
      );
      if (!claimed) {
        throw new ConflictError(
          'An upload with this Idempotency-Key is still in progress',
        );
      }
      savedDocument = claimed;
    }

    try {
      await this.storeUpload(savedDocument, leaseToken, res, {
        documentName,
        documentPath,
        orgId: orgId,
        isVersioned,
        fileExtension,
        buffer,
        mimeType,
      });
    } catch (error) {
      if (leaseToken !== undefined) {
        // Failed, not abandoned: the next retry need not wait out the lease.
        await releaseUpload(savedDocument._id, leaseToken).catch(
          (releaseError: unknown) => {
            logger.warn('Failed to release upload lease', {
              documentId: savedDocument._id,
              error: String(releaseError),
            });
          },
        );
      }
      throw error;
    }
  }

  private async storeUpload(
    savedDocument: Document,
    leaseToken: string | undefined,
    res: Response,
    upload: {
      documentName: string | undefined;
      documentPath: string | undefined;
      orgId: string;
      isVersioned: boolean;
      fileExtension: string;
      buffer: Buffer;
      mimeType: string;
    },
  ): Promise<void> {
    const {
      documentName,
      documentPath,
      orgId,
      isVersioned,
      fileExtension,
      buffer,
      mimeType,
    } = upload;
    const rootPath = getDocumentRootPath(
      orgId,
      savedDocument._id,
      documentPath,
    );
    const fullDocumentPath = getFullDocumentPath(orgId, documentPath);
    const concatenatedPath = getCurrentFilePath(
      rootPath,
      documentName ?? '',
      normalizeExtension(fileExtension),
      isVersioned,
    );

    let uploadResult: StorageServiceResponse<string>;
    try {
      uploadResult = await writeToStorage(
        this.storageServiceWrapper,
        {
          buffer,
          mimeType,
          documentPath: concatenatedPath,
          isVersioned,
        },
        { documentId: savedDocument._id },
      );
    } catch (error) {
      if (leaseToken === undefined) {
        // A failed write does not prove the file is absent: a request can time
        // out after storage kept it. Without an Idempotency-Key no retry can
        // take this document over, so remove it, but only once storage says
        // the path is empty.
        await this.removeUnstoredDocument(
          savedDocument._id,
          concatenatedPath,
        );
      }
      throw error;
    }
    const storedPath = uploadResult.data as string;
    const patch: DocumentPatch = { documentPath: fullDocumentPath };
    const addedVersions: DocumentVersion[] = [];

    const storageTypeKey = this.storageVendor;
    let normalizedUrl = '';
    let localPath = '';
    // Type-safe storage assignment
    if (isValidStorageVendor(storageTypeKey)) {
      // TODO : Move this to the local storage provider
      if (storageTypeKey === StorageVendor.Local) {
        const storageServiceEndpoint = await storedServiceEndpoint(
          this.keyValueStoreService,
          'storage',
          this.defaultConfig.endpoint,
        );
        localPath = storedPath;
        // normalize the url to the local storage
        const baseUrl = storedPath.replace(
          'file://',
          `${storageServiceEndpoint}/api/v1/document/${savedDocument._id}/download`,
        );
        // Remove everything after "download" if it exists
        normalizedUrl = baseUrl.split('/download')[0] + '/download';
        const storageInfo: StorageInfo = {
          url: normalizedUrl,
          localPath: localPath,
        };
        patch[storageTypeKey] = storageInfo;
      } else {
        const storageInfo: StorageInfo = { url: storedPath };
        patch[storageTypeKey] = storageInfo;
      }
    } else {
      throw new InternalServerError(`Invalid storage type: ${storageTypeKey}`);
    }

    if (isVersioned === false) {
      const saved = await this.saveUpload(
        savedDocument,
        patch,
        addedVersions,
        leaseToken,
      );
      res.status(200).json(saved);
      return;
    }

    if (savedDocument.versionHistory?.length === 0) {
      const nextVersion = savedDocument.versionHistory.length;
      const newDocumentFilePath = getVersionFilePath(
        rootPath,
        nextVersion,
        fileExtension,
      );

      const cloneResponse = await this.cloneDocument(
        savedDocument,
        buffer,
        newDocumentFilePath,
      );
      const versionLocalPath =
        storageTypeKey === StorageVendor.Local
          ? (cloneResponse.data ?? '')
          : '';
      // normalize the url to the local storage
      if (storageTypeKey === StorageVendor.Local) {
        cloneResponse.data = normalizedUrl;
      }

      if (cloneResponse.statusCode === HTTP_STATUS.OK && cloneResponse.data) {
        addedVersions.push({
          version: nextVersion,
          [`${storageTypeKey}`]: {
            url: cloneResponse.data,
            localPath:
              storageTypeKey === StorageVendor.Local
                ? versionLocalPath
                : localPath,
          },
          createdAt: Date.now(),
          size: savedDocument.sizeInBytes,
          extension: savedDocument.extension,
        });
      }
    }
    const saved = await this.saveUpload(
      savedDocument,
      patch,
      addedVersions,
      leaseToken,
    );
    res.status(200).json(saved);
  }

  /** The upload's final write; for a keyed one, only while it holds the lease. */
  private async saveUpload(
    document: Document,
    patch: DocumentPatch,
    pushVersions: DocumentVersion[],
    leaseToken: string | undefined,
  ): Promise<Document> {
    const saved = await getDocumentRepository().update(
      document.orgId,
      document._id,
      patch,
      { pushVersions, holdLease: leaseToken },
    );
    if (!saved) {
      // Without a lease nothing can have taken the upload over; the row was removed.
      if (leaseToken === undefined) {
        throw new NotFoundError('Document does not exist');
      }
      throw new ConflictError(
        'A later attempt with the same Idempotency-Key took this upload over',
      );
    }
    return saved;
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
    // Get mime type from document extension without the dot
    const ext = normalizeExtension(document.extension);
    const mimeType = getMimeType(ext.replace('.', ''));

    if (!mimeType) {
      throw new BadRequestError('Invalid document extension');
    }

    const cloneFilePayload: FilePayload = {
      buffer,
      mimeType,
      documentPath: newDocumentFilePath,
      isVersioned: document.isVersionedFile,
    };

    return writeToStorage(this.storageServiceWrapper, cloneFilePayload, {
      documentPath: newDocumentFilePath,
    });
  }
}
