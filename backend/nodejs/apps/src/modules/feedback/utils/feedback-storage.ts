import path from 'path';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { InternalServerError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { getFilenameWithoutExtension } from '../../../libs/utils/file-extension.util';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { Logger } from '../../../libs/services/logger.service';
import { loadConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { storageTypes } from '../../configuration_manager/constants/constants';
import { configPaths } from '../../configuration_manager/paths/paths';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import { StorageService } from '../../storage/storage.service';
import { StorageServiceAdapter } from '../../storage/adapter/base-storage.adapter';
import {
  AzureBlobStorageConfig,
  LocalStorageConfig,
  S3StorageConfig,
} from '../../storage/config/storage.config';
import { DocumentModel } from '../../storage/schema/document.schema';
import { Document, StorageInfo, StorageVendor } from '../../storage/types/storage.service.types';
import {
  getCurrentFilePath,
  getDocumentRootPath,
  getFullDocumentPath,
  getStorageVendor,
  isValidStorageVendor,
  normalizeExtension,
} from '../../storage/utils/utils';
import { endpoint } from '../../storage/constants/constants';
import { AppConfig } from '../../tokens_manager/config/config';
import mongoose from 'mongoose';

const logger = Logger.getInstance({ service: 'feedback.storage' });

export interface FeedbackStoredFile {
  fileName: string;
  mimeType: string;
  sizeInBytes: number;
  documentId: mongoose.Types.ObjectId;
}

async function createStorageAdapter(
  kvStore: KeyValueStoreService,
  appConfig: AppConfig,
): Promise<{ adapter: StorageServiceAdapter; storageVendor: StorageVendor }> {
  const raw = (await kvStore.get<string>(configPaths.storageService)) || '{}';
  const parsed = JSON.parse(raw) as {
    storageType?: string;
    s3?: string;
    azureBlob?: string;
    local?: string;
  };
  const storageVendor = getStorageVendor(parsed.storageType || storageTypes.LOCAL);
  const cmConfig = loadConfigurationManagerConfig();
  const encryption = EncryptionService.getInstance(cmConfig.algorithm, cmConfig.secretKey);

  let vendorConfig: S3StorageConfig | AzureBlobStorageConfig | LocalStorageConfig;
  if (storageVendor === StorageVendor.S3) {
    if (!parsed.s3) {
      throw new InternalServerError('S3 storage is not configured');
    }
    vendorConfig = JSON.parse(encryption.decrypt(parsed.s3)) as S3StorageConfig;
  } else if (storageVendor === StorageVendor.AzureBlob) {
    if (!parsed.azureBlob) {
      throw new InternalServerError('Azure Blob storage is not configured');
    }
    vendorConfig = JSON.parse(encryption.decrypt(parsed.azureBlob)) as AzureBlobStorageConfig;
  } else {
    vendorConfig = JSON.parse(parsed.local || '{}') as LocalStorageConfig;
  }

  const storageService = new StorageService(kvStore, vendorConfig, appConfig.storage);
  await storageService.initialize();
  const adapter = storageService.getAdapter();
  if (!adapter) {
    throw new InternalServerError('Storage service adapter not found');
  }
  return { adapter, storageVendor };
}

async function applyStorageInfo(
  kvStore: KeyValueStoreService,
  appConfig: AppConfig,
  savedDocument: DocumentModel,
  storageVendor: StorageVendor,
  uploadResultData: string,
): Promise<void> {
  if (storageVendor === StorageVendor.Local) {
    const url = (await kvStore.get<string>(endpoint)) || '{}';
    const storageServiceEndpoint =
      JSON.parse(url).storage?.endpoint || appConfig.storage.endpoint;
    const localPath = uploadResultData;
    const baseUrl = uploadResultData.replace(
      'file://',
      `${storageServiceEndpoint}/api/v1/document/${savedDocument._id}/download`,
    );
    const normalizedUrl = `${baseUrl.split('/download')[0]}/download`;
    const storageInfo: StorageInfo = { url: normalizedUrl, localPath };
    savedDocument[storageVendor] = storageInfo;
    return;
  }

  savedDocument[storageVendor] = { url: uploadResultData };
}

export async function uploadFeedbackAttachment(input: {
  kvStore: KeyValueStoreService;
  appConfig: AppConfig;
  orgId: string;
  userId: string;
  feedbackId: string;
  file: FileBufferInfo;
  mimeType: string;
}): Promise<FeedbackStoredFile> {
  const { adapter, storageVendor } = await createStorageAdapter(input.kvStore, input.appConfig);
  const originalName = input.file.originalname;
  const fileExtension = path.extname(originalName);
  const documentName = getFilenameWithoutExtension(originalName) || 'attachment';

  const savedDocument = await DocumentModel.create({
    documentName,
    orgId: new mongoose.Types.ObjectId(input.orgId),
    isVersionedFile: false,
    initiatorUserId: new mongoose.Types.ObjectId(input.userId),
    sizeInBytes: input.file.size,
    mimeType: input.mimeType,
    extension: fileExtension,
    createdAt: Date.now(),
    isDeleted: false,
    storageVendor,
    customMetadata: [
      { key: 'source', value: 'feedback' },
      { key: 'feedbackId', value: input.feedbackId },
    ],
  });

  const documentPath = `Feedback/${input.feedbackId}`;
  const rootPath = getDocumentRootPath(input.orgId, String(savedDocument._id), documentPath);
  const fullDocumentPath = getFullDocumentPath(input.orgId, documentPath);
  const concatenatedPath = getCurrentFilePath(
    rootPath,
    documentName,
    normalizeExtension(fileExtension),
    false,
  );

  const uploadResult = await adapter.uploadDocumentToStorageService({
    buffer: input.file.buffer,
    mimeType: input.mimeType,
    documentPath: concatenatedPath,
    isVersioned: false,
  });

  if (uploadResult.statusCode !== HTTP_STATUS.OK || !uploadResult.data) {
    await DocumentModel.deleteOne({ _id: savedDocument._id });
    throw new InternalServerError(uploadResult.msg || 'Failed to upload feedback attachment');
  }

  if (!isValidStorageVendor(storageVendor)) {
    await DocumentModel.deleteOne({ _id: savedDocument._id });
    throw new InternalServerError(`Invalid storage type: ${storageVendor}`);
  }

  savedDocument.documentPath = fullDocumentPath;
  await applyStorageInfo(
    input.kvStore,
    input.appConfig,
    savedDocument,
    storageVendor,
    uploadResult.data,
  );
  await savedDocument.save();

  return {
    fileName: originalName,
    mimeType: input.mimeType,
    sizeInBytes: input.file.size,
    documentId: savedDocument._id as mongoose.Types.ObjectId,
  };
}

export async function readFeedbackAttachmentBuffer(input: {
  kvStore: KeyValueStoreService;
  appConfig: AppConfig;
  orgId: string;
  documentId: string;
}): Promise<Buffer | null> {
  try {
    const document = await DocumentModel.findOne({
      _id: input.documentId,
      orgId: new mongoose.Types.ObjectId(input.orgId),
      isDeleted: false,
    }).lean<Document>();
    if (!document) {
      return null;
    }

    const { adapter } = await createStorageAdapter(input.kvStore, input.appConfig);
    const result = await adapter.getBufferFromStorageService(document);
    if (result.statusCode !== HTTP_STATUS.OK || !result.data) {
      return null;
    }
    return result.data;
  } catch (error) {
    logger.warn('Failed to read feedback attachment from storage', {
      documentId: input.documentId,
      error: error instanceof Error ? error.message : error,
    });
    return null;
  }
}
