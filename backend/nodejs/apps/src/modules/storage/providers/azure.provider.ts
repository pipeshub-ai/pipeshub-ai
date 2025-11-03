import {
  BlobServiceClient,
  StorageSharedKeyCredential,
} from '@azure/storage-blob';
import { injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { StorageServiceInterface } from '../services/storage.service';
import {
  FilePayload,
  StorageServiceResponse,
  Document,
} from '../types/storage.service.types';
import {
  StorageError,
  StorageConfigurationError,
  StorageUploadError,
  StorageDownloadError,
  StorageNotFoundError,
  StorageValidationError,
  MultipartUploadError,
  PresignedUrlError,
} from '../../../libs/errors/storage.errors';
import { AzureBlobStorageConfig } from '../config/storage.config';
import { encodeRFC5987 } from '../utils/utils';

@injectable()
class AzureBlobStorageAdapter implements StorageServiceInterface {
  private blobServiceClient: BlobServiceClient;
  private containerClient: any;
  private readonly containerName!: string;
  private readonly logger = Logger.getInstance({
    service: 'AzureBlobStorageAdapter',
  });
  constructor(credentials: Partial<AzureBlobStorageConfig>) {
    try {
      const {
        azureBlobConnectionString,
        accountName,
        accountKey,
        containerName,
        endpointProtocol,
        endpointSuffix,
      } = credentials;

      if (azureBlobConnectionString) {
        if (!containerName) {
          throw new StorageConfigurationError(
            'Missing required Azure credentials',
            {
              missingFields: {
                containerName: !containerName,
              },
            },
          );
        }
        this.blobServiceClient = BlobServiceClient.fromConnectionString(
          azureBlobConnectionString,
        );
      } else {
        if (!accountName || !accountKey || !containerName) {
          throw new StorageConfigurationError(
            'Missing required Azure credentials',
            {
              missingFields: {
                accountName: !accountName,
                accountKey: !accountKey,
                containerName: !containerName,
              },
            },
          );
        }
        // Create shared key credential
        const sharedKeyCredential = new StorageSharedKeyCredential(
          accountName,
          accountKey,
        );

        // Create blob service client
        this.blobServiceClient = new BlobServiceClient(
          `${endpointProtocol}://${accountName}.blob.${endpointSuffix}`,
          sharedKeyCredential,
        );
      }
      // Initialize container client
      this.containerName = containerName;
      this.containerClient =
        this.blobServiceClient.getContainerClient(containerName);

      // Ensure container exists
      this.ensureContainerExists();

      this.logger.info('Azure Blob Storage adapter initialized', {
        account: accountName,
        container: containerName,
      });
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageConfigurationError(
        'Failed to initialize Azure Blob Storage adapter',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  /**
   * Private method to ensure container exists
   * - Attempts to create container if it doesn't exist
   * - Logs success or existing container status
   * - Throws error if container operations fail
   */
  private async ensureContainerExists(): Promise<void> {
    try {
      const createContainerResponse =
        await this.containerClient.createIfNotExists();
      if (createContainerResponse.succeeded) {
        this.logger.info(
          `Container "${this.containerName}" created successfully`,
        );
      } else {
        this.logger.info(`Container "${this.containerName}" already exists`);
      }
    } catch (error) {
      throw new StorageConfigurationError('Failed to create/check container', {
        containerName: this.containerName,
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Upload document to Azure Blob Storage
   * @param fileInPayload
   * @returns StatusCode 200 and blob URL on success
   * @throws Error on upload failure
   */
  async uploadDocumentToStorageService(
    documentInPayload: FilePayload,
  ): Promise<StorageServiceResponse<string>> {
    try {
      this.validateFilePayload(documentInPayload);

      const blobClient = this.containerClient.getBlockBlobClient(
        documentInPayload.documentPath,
      );

      const uploadOptions = {
        blobHTTPHeaders: {
          blobContentType: documentInPayload.mimeType,
        },
      };

      await blobClient.uploadData(documentInPayload.buffer, uploadOptions);
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('Azure Blob Storage upload successful', {
          path: documentInPayload.documentPath,
          url: blobClient.url,
        });
      }

      return {
        statusCode: 200,
        data: blobClient.url,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageUploadError(
        'Failed to upload document to Azure Blob Storage',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  /**
   * Update existing document's content
   * @param bufferDataInPayLoad New content buffer
   * @param document Document metadata including path and extension
   * @returns StatusCode 200 and updated URL on success
   * @throws Error if document not found or update fails
   */
  async updateBuffer(
    bufferDataInPayLoad: Buffer,
    document: Document,
  ): Promise<StorageServiceResponse<string>> {
    try {
      if (!document.azureBlob?.url) {
        throw new StorageNotFoundError('Azure Blob Storage URL not found');
      }

      const blobPath = this.getBlobPath(document.azureBlob.url);
      const blobClient = this.containerClient.getBlockBlobClient(blobPath);

      const uploadOptions = {
        blobHTTPHeaders: {
          blobContentType: document.mimeType,
        },
      };

      await blobClient.uploadData(bufferDataInPayLoad, uploadOptions);
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('Azure Blob Storage update successful', {
          path: blobPath,
          url: blobClient.url,
        });
      }

      return {
        statusCode: 200,
        data: blobClient.url,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageUploadError(
        'Failed to update document in Azure Blob Storage',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  /**
   * Retrieve document content
   * @param document Document metadata
   * @param version Optional version number
   * @returns StatusCode 200 and document buffer on success
   * @throws Error if document not found or retrieval fails
   * Handles both current and versioned document retrieval
   */
  async getBufferFromStorageService(
    document: Document,
    version?: number,
  ): Promise<StorageServiceResponse<Buffer>> {
    try {
      const blobUrl =
        version === undefined || version === 0
          ? document.azureBlob?.url
          : document.versionHistory?.[version]?.azureBlob?.url;

      if (!blobUrl) {
        throw new StorageNotFoundError(
          'Azure Blob Storage URL not found for requested version',
        );
      }

      const blobPath = this.getBlobPath(blobUrl);
      const blobClient = this.containerClient.getBlockBlobClient(blobPath);

      const downloadResponse = await blobClient.download(0);

      if (!downloadResponse.readableStreamBody) {
        throw new StorageDownloadError('Retrieved blob has no content');
      }

      const buffer = await this.streamToBuffer(
        downloadResponse.readableStreamBody,
      );
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('Azure Blob Storage fetch successful', {
          path: blobPath,
        });
      }
      return {
        statusCode: 200,
        data: buffer,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageDownloadError(
        'Failed to get document from Azure Blob Storage',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  async getSignedUrl(
    document: Document,
    version?: number,
    fileName?: string,
    expirationTimeInSeconds: number = 3600,
  ): Promise<StorageServiceResponse<string>> {
    try {
      const blobUrl =
        version === undefined
          ? document.azureBlob?.url
          : document.versionHistory?.[version]?.azureBlob?.url;

      if (!blobUrl) {
        throw new StorageNotFoundError(
          'Azure Blob Storage URL not found for requested version',
        );
      }

      const blobPath = this.getBlobPath(blobUrl);
      const blobClient = this.containerClient.getBlockBlobClient(blobPath);

      // Prepare safe content-disposition for non-ASCII filenames
      const fullName = fileName ? `${fileName}${document.extension}` : undefined;
      const filenameStar = fullName ? encodeRFC5987(fullName) : undefined;

      // Generate SAS token
      const sasUrl = await blobClient.generateSasUrl({
        permissions: { read: true },
        expiresOn: new Date(Date.now() + expirationTimeInSeconds * 1000),
        ...(fullName && {
          contentDisposition: `attachment; filename*=UTF-8''${filenameStar}`,
        }),
      });

      return {
        statusCode: 200,
        data: sasUrl,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new PresignedUrlError(
        'Failed to generate signed URL for Azure Blob Storage',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  /**
   * Generate Shared Access Signature (SAS) URL
   * @param document Document metadata
   * @param version Optional version number
   * @param fileName Optional download filename
   * @param expirationTimeInSeconds URL expiration time (default 3600)
   * @returns StatusCode 200 and signed URL on success
   * @throws Error if URL generation fails
   * Supports content disposition for downloads
   */
  async getMultipartUploadId(
    _documentPath: string,
    _mimeType: string,
  ): Promise<StorageServiceResponse<{ uploadId: string }>> {
    throw new MultipartUploadError(
      'Multipart upload not implemented for Azure Blob Storage',
      { suggestion: 'Use direct upload instead' },
    );
  }

  /**
   * Generate URL for multipart upload part
   * @param documentPath Target path
   * @param partNumber Part number
   * @param uploadId Upload session ID
   * @returns StatusCode 501 - Not Implemented
   * Azure Blob Storage uses different upload mechanisms
   */
  async generatePresignedUrlForPart(
    _documentPath: string,
    _partNumber: number,
    _uploadId: string,
  ): Promise<StorageServiceResponse<{ url: string; partNumber: number }>> {
    throw new MultipartUploadError(
      'Multipart upload not implemented for Azure Blob Storage',
      { suggestion: 'Use direct upload instead' },
    );
  }

  /**
   * Complete multipart upload
   * @param documentPath Target path
   * @param uploadId Upload session ID
   * @param parts Array of uploaded parts
   * @returns StatusCode 501 - Not Implemented
   * Azure Blob Storage uses different upload mechanisms
   */
  async completeMultipartUpload(
    _documentPath: string,
    _uploadId: string,
    _parts: Array<{ ETag: string; PartNumber: number }>,
  ): Promise<StorageServiceResponse<{ url: string }>> {
    throw new MultipartUploadError(
      'Multipart upload not implemented for Azure Blob Storage',
      { suggestion: 'Use direct upload instead' },
    );
  }

  /**
   * Generate URL for direct upload
   * @param documentPath Target path
   * @returns StatusCode 200 and SAS URL for upload
   * @throws Error if URL generation fails
   * Preferred upload method for Azure Blob Storage
   */
  async generatePresignedUrlForDirectUpload(
    documentPath: string,
  ): Promise<StorageServiceResponse<{ url: string }>> {
    try {
      const blobClient = this.containerClient.getBlockBlobClient(documentPath);

      const sasUrl = await blobClient.generateSasUrl({
        permissions: { write: true },
        expiresOn: new Date(Date.now() + 3600000), // 1 hour
      });

      return {
        statusCode: 200,
        data: { url: sasUrl },
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new PresignedUrlError(
        'Failed to generate direct upload URL for Azure Blob Storage',
        {
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }

  private validateFilePayload(payload: FilePayload): void {
    if (!payload.buffer || !payload.documentPath || !payload.mimeType) {
      throw new StorageValidationError('Invalid file payload', {
        validation: {
          hasBuffer: !!payload.buffer,
          hasPath: !!payload.documentPath,
          hasMimeType: !!payload.mimeType,
        },
      });
    }
  }

  private async streamToBuffer(
    readableStream: NodeJS.ReadableStream,
  ): Promise<Buffer> {
    return new Promise((resolve, reject) => {
      const chunks: Buffer[] = [];
      readableStream.on('data', (data: Buffer) => {
        chunks.push(data instanceof Buffer ? data : Buffer.from(data));
      });
      readableStream.on('end', () => resolve(Buffer.concat(chunks)));
      readableStream.on('error', reject);
    });
  }

  private getBlobPath(url: string): string {
    try {
      const urlObj = new URL(url);
      const path = urlObj.pathname;
      // Remove container name from path and leading slash
      return path.replace(`/${this.containerName}/`, '');
    } catch (error) {
      throw new StorageValidationError(
        'Invalid Azure Blob Storage URL format',
        {
          url,
          container: this.containerName,
          originalError:
            error instanceof Error ? error.message : 'Unknown error',
        },
      );
    }
  }
}

export default AzureBlobStorageAdapter;
