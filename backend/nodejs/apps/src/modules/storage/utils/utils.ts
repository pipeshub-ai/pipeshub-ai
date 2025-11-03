import { DocumentModel } from '../schema/document.schema';
import { NextFunction, Response } from 'express';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import mongoose from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import { getMimeType } from '../mimetypes/mimetypes';
import { Document, StorageVendor } from '../types/storage.service.types';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { ErrorMetadata } from '../../../libs/errors/base.error';
import { createReadStream } from 'fs';
import { StorageServiceAdapter } from '../adapter/base-storage.adapter';
import { access } from 'fs';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';

const logger = Logger.getInstance({
  service: 'storage',
});

// Interface for document storage info response
export interface DocumentInfoResponse {
  document: mongoose.Document<unknown, {}, DocumentModel> & DocumentModel;
}

export function encodeRFC5987(str: string): string {
  return encodeURIComponent(str)
    .replace(/'/g, '%27')
    .replace(/\(/g, '%28')
    .replace(/\)/g, '%29')
    .replace(/\*/g, '%2A');
}

async function getDocumentInfoFromDb(
  documentId: string,
  orgId: mongoose.Types.ObjectId,
): Promise<DocumentInfoResponse | undefined> {
  try {
    // Validate documentId is a valid ObjectId
    if (!mongoose.isValidObjectId(documentId)) {
      throw new NotFoundError('Invalid document ID');
    }

    // Fetch the document from MongoDB
    const document = await DocumentModel.findOne({
      _id: documentId,
      orgId,
      isDeleted: false,
    });

    if (!document) {
      throw new NotFoundError('Document not found');
    }
    return { document };
  } catch (error) {
    if (
      error instanceof NotFoundError ||
      error instanceof InternalServerError
    ) {
      throw error;
    }

    const logger = Logger.getInstance();
    logger.error('Error fetching document:', {
      documentId,
      orgId,
      error: error instanceof Error ? error.message : 'Unknown error',
    });

    throw new InternalServerError('Error fetching document information');
  }
}

export async function getDocumentInfo(
  req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
  next: NextFunction,
): Promise<DocumentInfoResponse | undefined> {
  try {
    const orgId = extractOrgId(req);
    const documentId = req.params.documentId;

    const orgID = new mongoose.Types.ObjectId(orgId);
    if (!documentId) {
      throw new NotFoundError('Document ID is required');
    }
    const documentInfo = await getDocumentInfoFromDb(documentId, orgID);
    if (!documentInfo) {
      throw new NotFoundError('Document not found');
    }
    return documentInfo;
  } catch (error) {
    next(error);
    return Promise.reject(error);
  }
}

export function parseBoolean(
  value: string | boolean | undefined | null,
): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    return value.toLowerCase() === 'true';
  }
  return false;
}

export function isValidStorageVendor(vendor: string): vendor is StorageVendor {
  const validStorageTypes = [
    StorageVendor.S3,
    StorageVendor.AzureBlob,
    StorageVendor.Local,
  ];
  return validStorageTypes.includes(vendor as StorageVendor);
}

export function getExtension(documentName: string): string {
  // Handle edge cases where documentName is undefined, null or empty
  if (!documentName) {
    return '';
  }

  // Split by dot and get the last element
  const parts = documentName.split('.');

  // If there's no extension (no dots or ends with a dot), return empty string
  if (parts.length <= 1) {
    return '';
  }

  // Return the last element which is the extension
  return parts[parts.length - 1] || '';
}

export function hasExtension(documentName: string | undefined): boolean {
  if (documentName === undefined) {
    return false;
  }

  const extension = documentName.split('.').pop() || '';
  const mimeType = getMimeType(extension);
  return mimeType !== '';
}

/**
 * Validates file extension, MIME type, and document name constraints
 * @param extension - File extension (without leading dot)
 * @param documentName - Document name to validate (should not contain extension or forward slash)
 * @param fileNameForError - File name to use in error messages
 * @throws BadRequestError if validation fails
 */
export function validateFileAndDocumentName(
  extension: string,
  documentName: string | undefined,
  fileNameForError: string,
): void {
  // Validate MIME type support FIRST - most important check
  const mimeType = getMimeType(extension);
  if (mimeType === '') {
    throw new BadRequestError(
      `File "${fileNameForError}" has an unsupported file extension "${extension}". Supported file types include: .pdf, .docx, .xlsx, .csv, .md, .txt, .pptx, images, videos, and more.`,
    );
  }

  if (hasExtension(documentName)) {
    throw new BadRequestError(
      `File "${fileNameForError}": The document name cannot contain a file extension. Please provide only the name without the extension.`,
    );
  }

  if (documentName?.includes('/')) {
    throw new BadRequestError(
      `File "${fileNameForError}": The document name cannot contain a forward slash.`,
    );
  }
}

export async function createPlaceholderDocument(
  req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
  next: NextFunction,
  size: number,
  extension : string,
  originalname?: string,
): Promise<DocumentInfoResponse | undefined> {
  try {
    const {
      documentName,
      alternateDocumentName,
      documentPath,
      permissions,
      customMetadata,
      isVersionedFile,
    } = req.body as Partial<Document>;
    const orgId = extractOrgId(req);
    const userId = extractUserId(req);
    
    // Use originalname or documentName for error messages
    const fileNameForError = originalname || documentName || 'the file';
    
    // Validate file extension, MIME type, and document name constraints
    validateFileAndDocumentName(extension, documentName, fileNameForError);

    const documentInfo: Partial<Document> = {
      documentName,
      documentPath,
      alternateDocumentName,
      orgId: new mongoose.Types.ObjectId(orgId),
      isVersionedFile: isVersionedFile,
      permissions: permissions,
      initiatorUserId: userId ? new mongoose.Types.ObjectId(userId) : null,
      customMetadata,
      sizeInBytes: size,
      storageVendor: StorageVendor.S3,
      extension
    };

    const savedDocument = await DocumentModel.create(documentInfo);
    return { document: savedDocument };
  } catch (error) {
    next(error);
    return Promise.reject(error);
  }
}

export async function generatePresignedUrlForDirectUpload(
  adapter: StorageServiceAdapter,
  documentPath: string | undefined,
): Promise<string | undefined> {
  try {
    if (!documentPath) {
      throw new BadRequestError('Document path is required');
    }
    const presignedUrlResponse =
      await adapter.generatePresignedUrlForDirectUpload(documentPath);

    if (presignedUrlResponse.statusCode !== 200) {
      logger.error(
        'Error generating presigned URL:',
        presignedUrlResponse.data?.url,
      );
      const errorMetadata: ErrorMetadata = {
        statusCode: presignedUrlResponse.statusCode,
        requestedUrl: presignedUrlResponse.data?.url,
        errorMessage: presignedUrlResponse.msg,
        timestamp: new Date().toISOString(),
      };
      throw new InternalServerError(
        'Error generating presigned URL:',
        errorMetadata,
      );
    }
    return presignedUrlResponse.data?.url;
  } catch (error) {
    throw error;
  }
}

export function getBaseUrl(url: string): string | undefined {
  const baseUrl = url.split('?')[0];
  return baseUrl;
}

export function getStorageVendor(storageType: string): StorageVendor {
  switch (storageType) {
    case 's3':
      return StorageVendor.S3;
    case 'azureBlob':
      return StorageVendor.AzureBlob;
    case 'local':
      return StorageVendor.Local;
    default:
      throw new Error(`Invalid storage type: ${storageType}`);
  }
}

export function serveFileFromLocalStorage(document: Document, res: Response) {
  try {
    // Get the local file path directly from the document
    const localFilePath = document.local?.localPath;

    if (!localFilePath) {
      throw new NotFoundError('Local file path not found');
    }

    // Parse the file:// URL to get the actual filesystem path
    const urlObj = new URL(localFilePath);
    const fsPath = decodeURIComponent(urlObj.pathname);

    // Handle Windows paths by removing leading slash if needed
    const filePath =
      process.platform === 'win32' ? fsPath.replace(/^\//, '') : fsPath;

    // Check if file exists
    access(filePath, (err: any) => {
      if (err) {
        throw new NotFoundError('File not found');
      }
    });

    // convert the document.mimeType to a valid mime type
    const mimeType = getMimeType(document.extension);
    // Helper to safely encode Content-Disposition with UTF-8 filename support 
    const fullName = `${document.documentName}${document.extension}`;
    const filenameStar = encodeRFC5987(fullName);

    // Set appropriate headers - use UTF-8 filename* as primary, ASCII as fallback
    res.setHeader('Content-Type', mimeType || 'application/octet-stream');
    res.setHeader('Content-Disposition', `attachment; filename*=UTF-8''${filenameStar}`);

    // Stream the file directly to the response
    const fileStream = createReadStream(filePath);
    fileStream.pipe(res);

    // Handle potential errors during streaming
    fileStream.on('error', (error) => {
      logger.error('Error streaming file:', error);
      // Only send error if headers haven't been sent yet
      if (!res.headersSent) {
        res
          .status(HTTP_STATUS.INTERNAL_SERVER)
          .json({ error: 'Error streaming file' });
      }
    });
  } catch (error) {
    logger.error('Error serving local file:', error);
    throw error;
  }
}

export function extractOrgId(
  req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
): string {
  // Check user request first
  if ('user' in req && req.user && 'orgId' in req.user) {
    return req.user.orgId;
  }

  // Check service request
  if (
    'tokenPayload' in req &&
    req.tokenPayload &&
    'orgId' in req.tokenPayload
  ) {
    return req.tokenPayload.orgId;
  }

  throw new BadRequestError('Organization ID not found in request');
}

export function extractUserId(
  req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
): string | null {
  // Check user request first
  if ('user' in req && req.user && 'userId' in req.user) {
    return req.user.userId;
  }

  return null;
}
