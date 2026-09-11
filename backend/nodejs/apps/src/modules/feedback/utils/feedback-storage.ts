import path from 'path';
import { InternalServerError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { getFilenameWithoutExtension } from '../../../libs/utils/file-extension.util';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { Logger } from '../../../libs/services/logger.service';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import { StorageServiceAdapter } from '../../storage/adapter/base-storage.adapter';
import { DocumentModel } from '../../storage/schema/document.schema';
import { Document, StorageInfo, StorageVendor } from '../../storage/types/storage.service.types';
import {
  getCurrentFilePath,
  getDocumentRootPath,
  getFullDocumentPath,
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
  adapter: StorageServiceAdapter;
  storageVendor: StorageVendor;
  orgId: string;
  userId: string;
  feedbackId: string;
  file: FileBufferInfo;
  mimeType: string;
}): Promise<FeedbackStoredFile> {
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
    storageVendor: input.storageVendor,
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

  const uploadResult = await input.adapter.uploadDocumentToStorageService({
    buffer: input.file.buffer,
    mimeType: input.mimeType,
    documentPath: concatenatedPath,
    isVersioned: false,
  });

  if (uploadResult.statusCode !== HTTP_STATUS.OK || !uploadResult.data) {
    await DocumentModel.deleteOne({ _id: savedDocument._id });
    throw new InternalServerError(uploadResult.msg || 'Failed to upload feedback attachment');
  }

  if (!isValidStorageVendor(input.storageVendor)) {
    await DocumentModel.deleteOne({ _id: savedDocument._id });
    throw new InternalServerError(`Invalid storage type: ${input.storageVendor}`);
  }

  savedDocument.documentPath = fullDocumentPath;
  await applyStorageInfo(
    input.kvStore,
    input.appConfig,
    savedDocument,
    input.storageVendor,
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
  adapter: StorageServiceAdapter;
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

    const result = await input.adapter.getBufferFromStorageService(document);
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
