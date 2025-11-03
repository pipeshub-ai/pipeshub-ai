import { S3 } from 'aws-sdk';
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
import { Logger } from '../../../libs/services/logger.service';
import { encodeRFC5987 } from '../utils/utils';

/**
 * Implementation of StorageServiceInterface for Amazon S3
 * Handles file operations with Amazon S3 storage service
 */
class AmazonS3Adapter implements StorageServiceInterface {
  private readonly s3: S3;
  private readonly bucketName: string;
  private readonly region: string;
  private readonly logger = Logger.getInstance({ service: 'AmazonS3Adapter' });

  constructor(credentials: {
    accessKeyId: string;
    secretAccessKey: string;
    region: string;
    bucket: string;
  }) {
    try {
      const { accessKeyId, secretAccessKey, region, bucket } = credentials;

      // Validate required credentials
      if (!accessKeyId || !secretAccessKey || !region || !bucket) {
        throw new StorageConfigurationError('Missing required S3 credentials', {
          missingFields: {
            accessKeyId: !accessKeyId,
            secretAccessKey: !secretAccessKey,
            region: !region,
            bucket: !bucket,
          },
        });
      }

      // Initialize AWS S3 client
      this.s3 = new S3({
        accessKeyId,
        secretAccessKey,
        region,
      });

      this.bucketName = bucket;
      this.region = region;
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('S3 adapter initialized', {
          bucket: this.bucketName,
          region: this.region,
        });
      }
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageConfigurationError('Failed to initialize S3 adapter', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Uploads a document to Amazon S3 storage service.
   * @param documentInPayload - The document details and content to upload.
   * @returns A promise resolving to the S3 URL of the uploaded document.
   * @throws {StorageValidationError} If the payload is invalid
   * @throws {StorageUploadError} If the upload fails
   */
  async uploadDocumentToStorageService(
    documentInPayload: FilePayload,
  ): Promise<StorageServiceResponse<string>> {
    try {
      this.validateFilePayload(documentInPayload);

      const uploadParams = {
        Bucket: this.bucketName,
        Key: documentInPayload.documentPath,
        Body: documentInPayload.buffer,
        ContentType: documentInPayload.mimeType,
      };
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('Starting S3 upload', {
          path: documentInPayload.documentPath,
          size: documentInPayload.buffer.length,
        });
      }

      const result = await this.s3.upload(uploadParams).promise();

      if (!result?.Key) {
        throw new StorageUploadError('Upload response missing file key');
      }

      const fileUrl = this.getS3Url(result.Key);
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('S3 upload successful', {
          path: documentInPayload.documentPath,
          url: fileUrl,
        });
      }

      return {
        statusCode: 200,
        data: fileUrl,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageUploadError('Failed to upload document to S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Updates the content of an existing document in S3.
   * @param bufferDataInPayLoad - The new content for the document.
   * @param document - Metadata of the document to update.
   * @returns A promise resolving to the updated document's S3 URL.
   * @throws {StorageNotFoundError} If the document's S3 URL is not found
   * @throws {StorageUploadError} If the update fails
   */
  async updateBuffer(
    bufferDataInPayLoad: Buffer,
    document: Document,
  ): Promise<StorageServiceResponse<string>> {
    try {
      if (!document.s3?.url) {
        throw new StorageNotFoundError('Document S3 URL not found');
      }

      const key = this.extractKeyFromUrl(document.s3.url);

      const uploadParams = {
        Bucket: this.bucketName,
        Key: key,
        Body: bufferDataInPayLoad,
        ContentType: document.mimeType,
      };

      const result = await this.s3.upload(uploadParams).promise();

      if (!result?.Key) {
        throw new StorageUploadError('Update response missing file key');
      }

      const updatedUrl = this.getS3Url(result.Key);
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('S3 update successful', { key, updatedUrl });
      }
      return { statusCode: 200, data: updatedUrl };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageUploadError('Failed to update document in S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Retrieves the buffer content of a document from S3.
   * @param document - Metadata of the document to retrieve.
   * @param version - (Optional) The version of the document to retrieve.
   * @returns A promise resolving to the document's buffer content.
   * @throws {StorageNotFoundError} If the document's S3 URL is not found
   * @throws {StorageDownloadError} If the retrieval fails
   */
  async getBufferFromStorageService(
    document: Document,
    version?: number,
  ): Promise<StorageServiceResponse<Buffer>> {
    try {
      const s3Url =
        version === undefined || version === 0
          ? document.s3?.url
          : document.versionHistory?.[version]?.s3?.url;

      if (!s3Url) {
        throw new StorageNotFoundError(
          'S3 URL not found for requested version',
        );
      }

      const key = this.extractKeyFromUrl(s3Url);

      const response = await this.s3
        .getObject({
          Bucket: this.bucketName,
          Key: key,
        })
        .promise();

      if (!response.Body) {
        throw new StorageDownloadError('Retrieved object has no content');
      }
      if (process.env.NODE_ENV == 'development') {
        this.logger.info('S3 object fetched successfully', { key });
      }
      return {
        statusCode: 200,
        data: response.Body as Buffer,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new StorageDownloadError('Failed to get document from S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Initializes a multipart upload session in S3.
   * @param documentPath - The storage path for the document.
   * @param mimeType - The MIME type of the document.
   * @returns A promise resolving to the upload session ID.
   * @throws {MultipartUploadError} If initialization fails
   */
  async getMultipartUploadId(
    documentPath: string,
    mimeType: string,
  ): Promise<StorageServiceResponse<{ uploadId: string }>> {
    try {
      const params = {
        Bucket: this.bucketName,
        Key: documentPath,
        ContentType: mimeType,
      };

      const response = await this.s3.createMultipartUpload(params).promise();

      if (!response.UploadId) {
        throw new MultipartUploadError('Failed to get upload ID');
      }

      return {
        statusCode: 200,
        data: { uploadId: response.UploadId },
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new MultipartUploadError('Failed to initialize multipart upload', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Generates a presigned URL for uploading a part in a multipart session.
   * @param documentPath - The storage path for the document.
   * @param partNumber - The part number being uploaded.
   * @param uploadId - The multipart upload session ID.
   * @returns A promise resolving to the presigned URL and part number.
   * @throws {PresignedUrlError} If URL generation fails
   */
  async generatePresignedUrlForPart(
    documentPath: string,
    partNumber: number,
    uploadId: string,
  ): Promise<StorageServiceResponse<{ url: string; partNumber: number }>> {
    try {
      const params = {
        Bucket: this.bucketName,
        Key: documentPath,
        PartNumber: partNumber,
        UploadId: uploadId,
      };

      const url = await this.s3.getSignedUrlPromise('uploadPart', params);

      return {
        statusCode: 200,
        data: { url, partNumber },
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new PresignedUrlError('Failed to generate presigned URL for part', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Completes a multipart upload by combining all uploaded parts.
   * @param documentPath - The storage path for the document.
   * @param uploadId - The multipart upload session ID.
   * @param parts - List of parts with their ETags and part numbers.
   * @returns A promise resolving to the final document's S3 URL.
   * @throws {MultipartUploadError} If completion fails
   */
  async completeMultipartUpload(
    documentPath: string,
    uploadId: string,
    parts: Array<{ ETag: string; PartNumber: number }>,
  ): Promise<StorageServiceResponse<{ url: string }>> {
    try {
      const params = {
        Bucket: this.bucketName,
        Key: documentPath,
        UploadId: uploadId,
        MultipartUpload: { Parts: parts },
      };

      const response = await this.s3.completeMultipartUpload(params).promise();

      if (!response.Key) {
        throw new MultipartUploadError('Complete upload response missing key');
      }

      return {
        statusCode: 200,
        data: { url: this.getS3Url(response.Key) },
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new MultipartUploadError('Failed to complete multipart upload', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Generates a presigned URL for directly uploading a document.
   * @param documentPath - The storage path for the document.
   * @returns A promise resolving to the presigned URL.
   * @throws {PresignedUrlError} If URL generation fails
   */
  async generatePresignedUrlForDirectUpload(
    documentPath: string,
  ): Promise<StorageServiceResponse<{ url: string }>> {
    try {
      const params = {
        Bucket: this.bucketName,
        Key: documentPath,
        Expires: 3600, // 1 hour
      };

      const url = await this.s3.getSignedUrlPromise('putObject', params);

      return {
        statusCode: 200,
        data: { url },
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new PresignedUrlError('Failed to generate presigned URL', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Generates a signed URL for accessing or downloading a document.
   * @param document - Metadata of the document.
   * @param version - (Optional) The version of the document.
   * @param fileName - (Optional) The filename for download.
   * @param expirationTimeInSeconds - (Optional) Expiration time for the URL.
   * @returns A promise resolving to the signed URL.
   * @throws {StorageNotFoundError} If the document's S3 URL is not found
   * @throws {PresignedUrlError} If URL generation fails
   */
  async getSignedUrl(
    document: Document,
    version?: number,
    fileName?: string,
    expirationTimeInSeconds: number = 3600,
  ): Promise<StorageServiceResponse<string>> {
    try {
      if (!document) {
        throw new StorageNotFoundError('Document metadata is required');
      }

      const s3Url =
        version === undefined
          ? document.s3?.url
          : document.versionHistory?.[version]?.s3?.url;

      if (!s3Url) {
        throw new StorageNotFoundError(
          'S3 URL not found for requested version',
        );
      }

      const key = this.extractKeyFromUrl(s3Url);
      if (!key) {
        throw new PresignedUrlError('Failed to extract S3 key from URL');
      }

      const params: S3.GetObjectRequest = {
        Bucket: this.bucketName,
        Key: key,
      };

      if (fileName) {
        const fullName = `${fileName}${document.extension ?? ''}`;
        const filenameStar = encodeRFC5987(fullName);
        params.ResponseContentDisposition = `attachment; filename*=UTF-8''${filenameStar}`;
      }

      const signedUrl = await this.s3.getSignedUrlPromise('getObject', {
        ...params,
        Expires: expirationTimeInSeconds, // `Expires` is added here
      });

      return {
        statusCode: 200,
        data: signedUrl,
      };
    } catch (error) {
      if (error instanceof StorageError) {
        throw error;
      }
      throw new PresignedUrlError('Failed to generate signed URL', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Validates the completeness and correctness of a file payload.
   * @param payload - The file payload to validate.
   * @throws {StorageValidationError} If the payload is invalid
   */
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

  /**
   * Extracts the S3 key (path) from a full S3 URL.
   * Example: For URL 'https://bucket.s3.region.amazonaws.com/folder/file.pdf'
   * returns 'folder/file.pdf'
   *
   * @param url - The full S3 URL to parse
   * @returns The extracted S3 key (path portion of the URL)
   * @throws {StorageValidationError} If the URL format is invalid or doesn't match bucket/region
   */
  private extractKeyFromUrl(url: string): string {
    try {
      // Create regex pattern matching the bucket and region exactly
      const urlPattern = new RegExp(
        `https?://${this.bucketName}.s3.${this.region}.amazonaws.com/(.+)`,
      );
      const match = url.match(urlPattern);

      if (!match?.[1]) {
        throw new Error(
          `URL does not match expected pattern for bucket '${this.bucketName}' in region '${this.region}'`,
        );
      }

      // Decode any URL-encoded characters in the key
      const decodedKey = decodeURIComponent(match[1]);

      // Remove any trailing slashes
      return decodedKey.replace(/\/$/, '');
    } catch (error) {
      throw new StorageValidationError('Invalid S3 URL format', {
        url,
        bucket: this.bucketName,
        region: this.region,
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Generates a full S3 URL for a given key (path).
   * Example: For key 'folder/file.pdf' returns
   * 'https://bucket.s3.region.amazonaws.com/folder/file.pdf'
   *
   * @param key - The S3 key (path) to convert to a URL
   * @returns The full S3 URL for the object
   */
  private getS3Url(key: string): string {
    // Ensure the key is URL-encoded (except for forward slashes)
    const encodedKey = key
      .split('/')
      .map((part) => encodeURIComponent(part))
      .join('/');
    return `https://${this.bucketName}.s3.${this.region}.amazonaws.com/${encodedKey}`;
  }
}

export default AmazonS3Adapter;
