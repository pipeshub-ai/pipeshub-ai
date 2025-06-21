import { StorageServiceInterface } from '../services/storage.service';
import {
  Document,
  FilePayload,
  StorageServiceResponse,
} from '../types/storage.service.types';

/**
 * A class to wrap and delegate calls to a specific storage adapter implementing the StorageServiceInterface.
 *
 * This class acts as a centralized handler for file operations by leveraging a specified adapter.
 * It delegates calls to the adapter to execute storage service operations such as uploading, updating,
 * retrieving, and generating URLs for files.
 */
export class StorageServiceAdapter {
  private adapter: StorageServiceInterface;

  constructor(adapter: StorageServiceInterface) {
    this.adapter = adapter;
  }

  /**
   * Uploads a document to the storage service.
   * @param documentInPayload - The document details and content to upload.
   * @returns A promise resolving to the upload response.
   */
  uploadDocumentToStorageService(
    documentInPayload: FilePayload,
  ): Promise<StorageServiceResponse<string>> {
    return this.adapter.uploadDocumentToStorageService(documentInPayload);
  }

  /**
   * Updates the content of an existing document.
   * @param bufferDataInPayLoad - The new content for the document.
   * @param document - Metadata of the document to update.
   * @returns A promise resolving to the update response.
   */
  updateBuffer(
    bufferDataInPayLoad: Buffer,
    document: Document,
  ): Promise<StorageServiceResponse<string>> {
    return this.adapter.updateBuffer(bufferDataInPayLoad, document);
  }

  /**
   * Retrieves the buffer content of a document.
   * @param document - Metadata of the document to retrieve.
   * @param version - (Optional) The version of the document to retrieve.
   * @returns A promise resolving to the buffer content.
   */
  getBufferFromStorageService(
    document: Document,
    version?: number,
  ): Promise<StorageServiceResponse<Buffer>> {
    return this.adapter.getBufferFromStorageService(document, version);
  }

  /**
   * Generates a signed URL for accessing or downloading a document.
   * @param document - Metadata of the document.
   * @param version - (Optional) The version of the document.
   * @param fileName - (Optional) The filename for download.
   * @param expirationTimeInSeconds - (Optional) Expiration time for the URL.
   * @returns A promise resolving to the signed URL.
   */
  getSignedUrl(
    document: Document,
    version?: number,
    fileName?: string,
    expirationTimeInSeconds?: number,
  ): Promise<StorageServiceResponse<string>> {
    return this.adapter.getSignedUrl(
      document,
      version,
      fileName,
      expirationTimeInSeconds,
    );
  }

  /**
   * Initializes a multipart upload session.
   * @param documentPath - The storage path for the document.
   * @param mimeType - The MIME type of the document.
   * @returns A promise resolving to the upload session ID.
   */
  getMultipartUploadId(
    documentPath: string,
    mimeType: string,
  ): Promise<StorageServiceResponse<{ uploadId: string }>> {
    return this.adapter.getMultipartUploadId
      ? this.adapter.getMultipartUploadId(documentPath, mimeType)
      : Promise.reject(new Error('Method not implemented'));
  }

  /**
   * Generates a presigned URL for uploading a part in a multipart session.
   * @param documentPath - The storage path for the document.
   * @param partNumber - The part number being uploaded.
   * @param uploadId - The multipart upload session ID.
   * @returns A promise resolving to the presigned URL.
   */
  generatePresignedUrlForPart(
    documentPath: string,
    partNumber: number,
    uploadId: string,
  ): Promise<StorageServiceResponse<{ url: string; partNumber: number }>> {
    return this.adapter.generatePresignedUrlForPart
      ? this.adapter.generatePresignedUrlForPart(
          documentPath,
          partNumber,
          uploadId,
        )
      : Promise.reject(new Error('Method not implemented'));
  }

  /**
   * Completes a multipart upload by combining all uploaded parts.
   * @param documentPath - The storage path for the document.
   * @param uploadId - The multipart upload session ID.
   * @param parts - List of parts with their ETags and part numbers.
   * @returns A promise resolving to the final document URL.
   */
  completeMultipartUpload(
    documentPath: string,
    uploadId: string,
    parts: Array<{ ETag: string; PartNumber: number }>,
  ): Promise<StorageServiceResponse<{ url: string }>> {
    return this.adapter.completeMultipartUpload
      ? this.adapter.completeMultipartUpload(documentPath, uploadId, parts)
      : Promise.reject(new Error('Method not implemented'));
  }

  /**
   * Generates a presigned URL for directly uploading a document.
   * @param documentPath - The storage path for the document.
   * @returns A promise resolving to the presigned URL.
   */
  generatePresignedUrlForDirectUpload(
    documentPath: string,
  ): Promise<StorageServiceResponse<{ url: string }>> {
    return this.adapter.generatePresignedUrlForDirectUpload
      ? this.adapter.generatePresignedUrlForDirectUpload(documentPath)
      : Promise.reject(new Error('Method not implemented'));
  }
}
