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
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  Document,
  DocumentVersion,
  FilePayload,
  StorageServiceResponse,
  StorageVendor,
} from '../types/storage.service.types';
import { StorageService } from '../storage.service';
import {
  getCurrentFilePath,
  DocumentInfoResponse,
  getDocumentRootPath,
  extractOrgId,
  extractUserId,
  getBaseUrl,
  getDocumentInfo,
  getFullDocumentPath,
  getStorageVendor,
  getVersionFilePath,
  hasExtension,
  isValidStorageVendor,
  normalizeExtension,
  serveFileFromLocalStorage,
} from '../utils/utils';
import { UploadDocumentService } from './storage.upload.service';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import { DocumentModel } from '../schema/document.schema';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { getMimeType } from '../mimetypes/mimetypes';
import path from 'path';
import { ErrorMetadata } from '../../../libs/errors/base.error';
import { StorageError } from '../../../libs/errors/storage.errors';

// Shape of a document row selected for a moveTree operation. documentPath is
// non-optional here (unlike the base Document type) because every row comes
// from a query filtered on documentPath.
interface MatchedTreeDocument {
  _id: unknown;
  documentPath: string;
  documentName: string;
  extension?: string;
  isVersionedFile?: boolean;
  versionHistory?: DocumentVersion[];
}

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
    // OPTIMIZATION: Parallelize both buffer reads
    const [response1, response2] = await Promise.all([
      adapter.getBufferFromStorageService(document, version1),
      adapter.getBufferFromStorageService(document, version2),
    ]);
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

  /**
   * getStorageConfig's cached return shape (AzureBlobStorageConfig |
   * LocalStorageConfig | S3StorageConfig) doesn't carry `storageType` --
   * mirror uploadDocument's pattern of reading it straight from etcd instead.
   */
  private async getConfiguredStorageType(
    _req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
  ): Promise<string> {
    const storageConfig =
      (await this.keyValueStoreService.get<string>(storageEtcdPaths)) || '{}';
    const { storageType } = JSON.parse(storageConfig);
    return storageType ?? 'local';
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
      if (hasExtension(documentName, extension)) {
        throw new BadRequestError(
          'The name of the document cannot have extensions',
        );
      }

      if (documentName?.includes('/')) {
        throw new BadRequestError(
          'The name of the document cannot have forward slash',
        );
      }

      const fullDocumentPath = getFullDocumentPath(orgId, documentPath);
      const storageConfig =
        (await this.keyValueStoreService.get<string>(storageEtcdPaths)) || '{}';
      const { storageType } = JSON.parse(storageConfig);
      const storageVendor = getStorageVendor(storageType ?? '');
      const documentInfo: Partial<Document> = {
        documentName,
        documentPath: fullDocumentPath,
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

  async moveTree(
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = extractOrgId(req);
      const { oldPath, newPath } = req.body as {
        oldPath: string;
        newPath: string;
      };

      if (!oldPath) {
        throw new BadRequestError('oldPath is required');
      }
      if (!newPath) {
        throw new BadRequestError('newPath is required');
      }

      if (oldPath.includes('..') || newPath.includes('..')) {
        throw new BadRequestError('oldPath/newPath must not contain ".." segments');
      }

      const oldFullPath = getFullDocumentPath(String(orgId), oldPath);
      const newFullPath = getFullDocumentPath(String(orgId), newPath);

      // Moving a tree under itself (e.g. a/b -> a/b/c) would make the
      // post-move delete of the old root also wipe the freshly-written new
      // objects nested beneath it. Reject rather than corrupt.
      if (newFullPath.startsWith(`${oldFullPath}/`)) {
        throw new BadRequestError('newPath must not be a descendant of oldPath');
      }

      // '/' (0x2F) is immediately followed by '0' (0x30) in ASCII -- this
      // range covers every documentPath that starts with "oldFullPath/"
      // without also matching an unrelated sibling like "oldFullPath2/...".
      const descendantLower = `${oldFullPath}/`;
      const descendantUpper = `${oldFullPath}0`;

      // The query matches only on documentPath, so every result is guaranteed
      // to have one -- .lean<T>() reflects that, unlike the base Document
      // type where documentPath is optional. Org isolation comes from the
      // "${orgId}/PipesHub/" prefix baked into oldFullPath (orgId is not a
      // declared schema field, so it can't be filtered on reliably); the
      // isDeleted guard keeps soft-deleted rows from being relocated.
      const matched = await DocumentModel.find({
        isDeleted: { $ne: true },
        $or: [
          { documentPath: oldFullPath },
          { documentPath: { $gte: descendantLower, $lt: descendantUpper } },
        ],
      })
        .select('_id documentPath documentName extension isVersionedFile versionHistory')
        .lean<MatchedTreeDocument[]>();

      if (matched.length === 0) {
        res.status(HTTP_STATUS.OK).json({ moved: 0 });
        return;
      }

      const storageType = await this.getConfiguredStorageType(req);
      const adapter = await this.initializeStorageAdapter(req);

      let failedIds: string[] = [];
      if (storageType === 'local') {
        await this.moveTreeLocal(adapter, oldFullPath, newFullPath, matched, orgId);
      } else {
        ({ failedIds } = await this.moveTreeRemote(
          adapter,
          storageType,
          oldFullPath,
          newFullPath,
          matched,
          orgId,
        ));
      }

      // `failed` is only present when at least one document's blob could not
      // be relocated -- those documents were left fully unmoved (see
      // moveTreeRemote) and the caller should surface/retry them explicitly
      // rather than assume the whole tree moved cleanly.
      const response: { moved: number; failed?: string[] } = {
        moved: matched.length - failedIds.length,
      };
      if (failedIds.length > 0) {
        response.failed = failedIds;
      }
      res.status(HTTP_STATUS.OK).json(response);
    } catch (error) {
      next(error);
    }
  }

  // The server-side aggregation expression that rewrites a document's
  // `documentPath` by swapping the oldFullPath prefix for newFullPath while
  // preserving each row's own descendant suffix. Shared by the local and
  // remote bulk updates.
  private documentPathRewriteExpr(
    oldFullPath: string,
    newFullPath: string,
  ): Record<string, unknown> {
    return {
      $concat: [
        newFullPath,
        {
          $substrCP: [
            '$documentPath',
            oldFullPath.length,
            { $subtract: [{ $strLenCP: '$documentPath' }, oldFullPath.length] },
          ],
        },
      ],
    };
  }

  private async moveTreeLocal(
    adapter: StorageServiceAdapter,
    oldFullPath: string,
    newFullPath: string,
    matched: MatchedTreeDocument[],
    orgId: string | undefined,
  ): Promise<void> {
    await adapter.renameTree(oldFullPath, newFullPath);

    await DocumentModel.updateMany(
      { _id: { $in: matched.map((m) => m._id) } },
      [{ $set: { documentPath: this.documentPathRewriteExpr(oldFullPath, newFullPath) } }],
    );

    // documentPath is not consulted at read time -- downloads resolve blobs
    // from local.localPath (and versionHistory[*].local.localPath). Rewrite
    // those so the relocated files are actually reachable.
    await this.rewriteStoredUrls(adapter, StorageVendor.Local, matched, oldFullPath, newFullPath, orgId);
  }

  // For S3/Azure, renameObject is copy-then-delete-the-source: the old blob
  // is gone the instant it resolves. Each document's Mongo state (documentPath
  // + URL fields) is therefore committed IMMEDIATELY after that document's own
  // blob move succeeds -- never batched until after the loop -- so a failure
  // on document N can never leave an EARLIER document's blob moved while its
  // Mongo row still points at the (now-deleted) old location. A document that
  // fails is left fully unmoved (blob untouched, Mongo untouched) and its id
  // is collected so the caller can see which ones need a retry/manual look.
  private async moveTreeRemote(
    adapter: StorageServiceAdapter,
    storageType: string,
    oldFullPath: string,
    newFullPath: string,
    matched: MatchedTreeDocument[],
    orgId: string | undefined,
    batchSize = 200,
  ): Promise<{ failedIds: string[] }> {
    const failedIds: string[] = [];

    for (let i = 0; i < matched.length; i += batchSize) {
      const batch = matched.slice(i, i + batchSize);
      for (const doc of batch) {
        const docId = String(doc._id);
        const relativeSuffix = doc.documentPath.slice(oldFullPath.length);
        const docNewFullPath = `${newFullPath}${relativeSuffix}`;

        const oldRoot = getDocumentRootPath(
          String(orgId),
          docId,
          undefined,
          doc.documentPath,
        );
        const newRoot = getDocumentRootPath(
          String(orgId),
          docId,
          undefined,
          docNewFullPath,
        );

        // Same-path move: nothing to relocate (doc root and leaf filename
        // are both unchanged without a rename).
        if (oldRoot === newRoot) {
          continue;
        }

        const ext = normalizeExtension(doc.extension ?? '');
        const oldFilePath = getCurrentFilePath(
          oldRoot,
          doc.documentName,
          ext,
          !!doc.isVersionedFile,
        );
        const newFilePath = getCurrentFilePath(
          newRoot,
          doc.documentName,
          ext,
          !!doc.isVersionedFile,
        );

        try {
          if (doc.isVersionedFile) {
            await adapter.copyTree(`${oldRoot}/versions`, `${newRoot}/versions`);
          }
          await adapter.renameObject(oldFilePath, newFilePath);
        } catch (error) {
          // StorageError wraps the real provider error (e.g. the actual AWS
          // AccessDenied/NoSuchKey reason) in `metadata.originalError` --
          // `.message` alone is just the generic "Failed to rename object in
          // S3" wrapper text and hides the reason the operation failed.
          const detail =
            error instanceof StorageError
              ? (error.metadata?.['originalError'] ?? error.message)
              : ((error as Error)?.message ?? error);
          this.logger.warn(
            `moveTree: failed to relocate blob for document ${docId}; leaving it at its old path`,
            { documentId: docId, oldFilePath, newFilePath, error: detail },
          );
          failedIds.push(docId);
          continue;
        }

        // Commit this document's documentPath + URL fields in the same
        // write the moment its blob move succeeds -- this is the fix for the
        // data-loss window described above.
        const set: Record<string, unknown> = {
          documentPath: docNewFullPath,
          ...this.buildStorageUrlSet(adapter, storageType, doc, docNewFullPath, doc.documentName, orgId),
        };
        try {
          await DocumentModel.updateOne({ _id: doc._id }, { $set: set });
        } catch (error) {
          // The blob already moved but Mongo didn't take the update -- this
          // document is now genuinely inconsistent and needs manual repair.
          // Reporting it (instead of silently continuing) is the best we can
          // do without a cross-system transaction.
          this.logger.warn(
            `moveTree: blob relocated for document ${docId} but the Mongo update failed; document needs manual repair`,
            { documentId: docId, error: (error as Error)?.message ?? error },
          );
          failedIds.push(docId);
          continue;
        }

        // Only delete the old blob once Mongo already points at the new path
        // -- deleting before the update risked orphaning a row that still
        // referenced the (now-gone) old object if the process crashed in between.
        try {
          await adapter.deleteObject(oldRoot);
        } catch {
          // best-effort cleanup; a stale blob at the old path is not fatal
        }
      }
    }

    return { failedIds };
  }

  // Rewrites each document's stored blob-location field(s) to the new root.
  // Downloads resolve from s3.url / azureBlob.url / local.localPath (and the
  // per-version equivalents), never from documentPath, so these must be
  // rewritten alongside the physical relocation or moved docs 404.
  //
  // Only used by moveTreeLocal, where a single renameTree call relocates the
  // whole prefix atomically on disk -- there is no per-document blob move to
  // interleave with, so a batched rewrite afterward is safe. moveTreeRemote
  // moves one blob at a time and calls buildStorageUrlSet directly per
  // document instead, so each document's Mongo state can commit immediately
  // after its own blob move succeeds.
  private async rewriteStoredUrls(
    adapter: StorageServiceAdapter,
    storageType: string,
    docs: MatchedTreeDocument[],
    oldFullPath: string,
    newFullPath: string,
    orgId: string | undefined,
  ): Promise<void> {
    for (const doc of docs) {
      const docNewFullPath = `${newFullPath}${doc.documentPath.slice(oldFullPath.length)}`;
      const set = this.buildStorageUrlSet(
        adapter,
        storageType,
        doc,
        docNewFullPath,
        doc.documentName,
        orgId,
      );
      if (Object.keys(set).length > 0) {
        await DocumentModel.updateOne({ _id: doc._id }, { $set: set });
      }
    }
  }

  private buildStorageUrlSet(
    adapter: StorageServiceAdapter,
    storageType: string,
    doc: MatchedTreeDocument,
    docNewFullPath: string,
    nameForThisDoc: string,
    orgId: string | undefined,
  ): Record<string, unknown> {
    const ext = normalizeExtension(doc.extension ?? '');
    const newRoot = getDocumentRootPath(
      String(orgId),
      String(doc._id),
      undefined,
      docNewFullPath,
    );
    const liveKey = getCurrentFilePath(newRoot, nameForThisDoc, ext, !!doc.isVersionedFile);
    const liveUrl = adapter.getObjectUrl(liveKey);

    const set: Record<string, unknown> = {};
    // local.url is an _id-based download endpoint that never changes on move;
    // only local.localPath (the concrete file:// path) must be rewritten.
    if (storageType === StorageVendor.Local) {
      set['local.localPath'] = liveUrl;
    } else if (storageType === StorageVendor.AzureBlob) {
      set['azureBlob.url'] = liveUrl;
    } else {
      set['s3.url'] = liveUrl;
    }

    if (doc.isVersionedFile && doc.versionHistory?.length) {
      set.versionHistory = doc.versionHistory.map((v) => {
        const vExt = normalizeExtension(v.extension ?? doc.extension ?? '');
        const versionUrl = adapter.getObjectUrl(
          getVersionFilePath(newRoot, v.version ?? 0, vExt),
        );
        if (storageType === StorageVendor.Local) {
          return { ...v, local: { url: v.local?.url ?? '', localPath: versionUrl } };
        }
        if (storageType === StorageVendor.AzureBlob) {
          return { ...v, azureBlob: { ...(v.azureBlob ?? {}), url: versionUrl } };
        }
        return { ...v, s3: { ...(v.s3 ?? {}), url: versionUrl } };
      });
    }
    return set;
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
      const currentVersionNote = req.body.currentVersionNote;
      const nextVersionNote = req.body.nextVersionNote;
      const userId = extractUserId(req);
      const orgId = extractOrgId(req);
      const docResult: DocumentInfoResponse | undefined = await getDocumentInfo(
        req,
        next,
      );

      if (!docResult) {
        throw new NotFoundError('Error fetching document from db');
      }

      const document = docResult.document;
      const storageType = document.storageVendor;

      if (document.isVersionedFile === false) {
        throw new BadRequestError('This document cannot be versioned');
      }

      const uploadedFileExtension = path.extname(originalname);

      const normalizedDocumentExtension = normalizeExtension(
        document.extension ?? '',
      ).trim().toLowerCase();
      const normalizedUploadedExtension = normalizeExtension(
        uploadedFileExtension,
      ).trim().toLowerCase();
      if (normalizedDocumentExtension !== normalizedUploadedExtension) {
        throw new BadRequestError(`Uploaded file extension ${uploadedFileExtension} does not match the original document extension ${document.extension}`);
      }

      const adapter = await this.initializeStorageAdapter(req);
      const basePath = getDocumentRootPath(
        String(orgId),
        String(document._id),
        "",
        document.documentPath,
      );
      const ext = normalizeExtension(document.extension ?? '');

      // No versions yet (e.g. presigned upload): save current as v0 first so we don't overwrite it
      if (!document.versionHistory?.length) {
        const versionFilePath = getVersionFilePath(basePath, 0, ext);
        const bufferResponse = await adapter.getBufferFromStorageService(
          document,
          undefined,
        );

        if (bufferResponse.statusCode !== 200) {
          throw new InternalServerError(
            `Some error occurred while uploading next version: ${bufferResponse.msg}`,
          );
        }

        const response = await this.cloneDocument(
          document,
          bufferResponse.data as Buffer,
          versionFilePath,
          next,
          adapter,
        );

        if (!response || response.statusCode !== 200) {
          throw new InternalServerError(
            response?.data ?? 'Failed to save current as v0 before update',
          );
        }

        const storageConfig =
          (await this.keyValueStoreService.get<string>(storageEtcdPaths)) ||
          '{}';
        const { storageType: configStorageType } = JSON.parse(storageConfig);

        document.versionHistory = document.versionHistory ?? [];
        document.versionHistory.push({
          version: 0,
          [configStorageType]: {
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
      } else {
        // Check if document changed (current vs last version)
        const isDocumentChanged = !(await this.compareDocuments(
          document,
          undefined,
          document.versionHistory.length - 1,
          adapter,
        ));

        // If current document was modified since last version, save it as a new version first
        if (isDocumentChanged === true) {
        const versionToSave = document.versionHistory?.length ?? 0;
        const versionFilePath = getVersionFilePath(
          basePath,
          versionToSave,
          ext,
        );
        const bufferResponse = await adapter.getBufferFromStorageService(
          document,
          undefined,
        );

        if (bufferResponse.statusCode !== 200) {
          throw new InternalServerError(
            `Some error occurred while uploading next version: ${bufferResponse.msg}`,
          );
        }

        const response = await this.cloneDocument(
          document,
          bufferResponse.data as Buffer,
          versionFilePath,
          next,
          adapter,
        );

        if (!response || response.statusCode !== 200) {
          throw new InternalServerError(
            response?.data ?? 'Failed to save current version before update',
          );
        }

        document.versionHistory?.push({
          version: versionToSave,
          [document.storageVendor]: {
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
        }
      }

      // Now upload the new file - parallelize version and current writes
      const nextVersion = document.versionHistory?.length ?? 0;
      const versionFilePath = getVersionFilePath(basePath, nextVersion, ext);
      const currentFilePath = getCurrentFilePath(
        basePath,
        document.documentName ?? '',
        ext,
        !!document.isVersionedFile,
      );

      const nextVersionPayload: FilePayload = {
        buffer: buffer,
        mimeType: mimetype,
        documentPath: versionFilePath,
        isVersioned: document.isVersionedFile,
      };

      const currentPayload: FilePayload = {
        buffer: buffer,
        mimeType: mimetype,
        documentPath: currentFilePath,
        isVersioned: document.isVersionedFile,
      };

      // OPTIMIZATION: Upload to both locations in parallel
      const [versionResponse, currentResponse] = await Promise.all([
        adapter.uploadDocumentToStorageService(nextVersionPayload),
        adapter.uploadDocumentToStorageService(currentPayload),
      ]);

      if (versionResponse.statusCode !== 200) {
        throw new InternalServerError(
          `Failed to upload version file: ${versionResponse.msg}`,
        );
      }

      if (currentResponse.statusCode !== 200) {
        throw new InternalServerError(
          `Failed to upload current file: ${currentResponse.msg}`,
        );
      }

      const fileExtension = path.extname(originalname);

      // Update document metadata
      document.mutationCount = (document.mutationCount ?? 0) + 1;
      document.sizeInBytes = size;

      document.versionHistory?.push({
        version: nextVersion,
        [document.storageVendor]: {
          url: versionResponse?.data,
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
      if (storageType === StorageVendor.S3 && currentResponse?.data) {
        document.s3 = { url: currentResponse.data };
      } else if (storageType === StorageVendor.AzureBlob && currentResponse?.data) {
        document.azureBlob = { url: currentResponse.data };
      }

      // Single save at the end
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
      // Version is validated by RollBackToPreviousVersionSchema and available as a number in query/body.
      const version =
        (req.query.version as number | undefined) ??
        (req.body as { version?: number })?.version;
      const { note } = req.body as { note: string };
      const userId = extractUserId(req);
      const orgId = extractOrgId(req);
      const docResult: DocumentInfoResponse | undefined = await getDocumentInfo(
        req,
        next,
      );
      if (!docResult) {
        throw new NotFoundError('Document Id does not exist');
      }

      const document = docResult.document;

      if (!document.isVersionedFile) {
        throw new BadRequestError(
          'This is a non-versioned document, no previous exists',
        );
      }

      if (version === undefined || version === null) {
        throw new BadRequestError(
          'version is required for rollback (body: { "version": 0, "note": "..." })',
        );
      }

      const versionNum = version as number;
      const currentVersion = document.versionHistory?.length ?? 0;

      if (versionNum >= currentVersion - 1) {
        throw new BadRequestError(
          `Cannot rollback to version ${versionNum}: current latest is version ${currentVersion - 1}`,
        );
      }
      const adapter = await this.initializeStorageAdapter(req);
      const basePath = getDocumentRootPath(
        String(orgId),
        String(document._id),
        "",
        document.documentPath,
      );
      const ext = normalizeExtension(document.extension ?? '');

      const bufferResult = await adapter.getBufferFromStorageService(
        document,
        versionNum,
      );

      if (bufferResult.statusCode !== HTTP_STATUS.OK) {
        throw new InternalServerError(
          `Some error occurred while rollback: ${bufferResult.msg}`,
        );
      }

      const currentFileResponse = await this.cloneDocument(
        document,
        bufferResult.data as Buffer,
        getCurrentFilePath(basePath, document.documentName ?? '', ext, true),
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
      const newDocumentFilePath = getVersionFilePath(
        basePath,
        nextVersion,
        ext,
      );
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

      if (!isValidStorageVendor(document.storageVendor)) {
        throw new BadRequestError(
          `Invalid storage type: ${document.storageVendor}`,
        );
      }
      document.mutationCount = (document.mutationCount ?? 0) + 1;
      document.versionHistory = document.versionHistory ?? [];
      document.versionHistory.push({
        version: nextVersion,
        [document.storageVendor]: {
          url: response?.data,
        },
        mutationCount: document.mutationCount,
        extension: document.extension,
        note: note,
        size: document.versionHistory[versionNum]?.size,
        initiatedByUserId: userId
          ? (new mongoose.Types.ObjectId(
              userId,
            ) as unknown as mongoose.Schema.Types.ObjectId)
          : undefined,
        createdAt: Date.now(),
      });

      document.sizeInBytes = document.versionHistory[versionNum]?.size;

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
      const basePath = document.documentPath.endsWith(String(documentId))
        ? document.documentPath
        : `${document.documentPath}/${documentId}`;
      const ext = normalizeExtension(document.extension ?? '');
      const pathForUpload = getCurrentFilePath(
        basePath,
        document.documentName ?? '',
        ext,
        !!document.isVersionedFile,
      );
      const presignedUrlResponse =
        await adapter.generatePresignedUrlForDirectUpload(pathForUpload);

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

      // for not versioned files, mutationCount>1 means the file was changed
      if (!document.isVersionedFile || !document.versionHistory?.length) {
        res
          .status(HTTP_STATUS.OK)
          .json((document.mutationCount ?? 1) > 1);
        return;
      }

      const adapter = await this.initializeStorageAdapter(req);
      const isDocumentChanged = !(await this.compareDocuments(
        document,
        undefined,
        document.versionHistory.length - 1,
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