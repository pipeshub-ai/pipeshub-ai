import { randomUUID } from 'crypto';
import {
  PAID_RESULT_CALL_DEADLINE_MS,
  withDeadline,
} from '../utils/call-deadline';
import {
  createS3Service as buildS3Service,
  type S3Service,
  type S3ServiceConfig,
} from './s3-client';
import { StorageServiceInterface } from '../services/storage.service';
import {
  FilePayload,
  StorageServiceResponse,
  Document,
  ObjectPage,
  ConditionalCapabilities,
  ConditionalDeleteResult,
  SignObjectUrlOptions,
  SignedObjectUrl,
} from '../types/storage.service.types';
import {
  StorageError,
  StorageConfigurationError,
  StorageUploadError,
  StorageDownloadError,
  StorageNotFoundError,
  StorageValidationError,
  StorageUrlParseError,
  MultipartUploadError,
  PresignedUrlError,
} from '../../../libs/errors/storage.errors';
import { Logger } from '../../../libs/services/logger.service';
import { encodeRFC5987 } from '../utils/utils';
import {
  normalizeEtag,
  quoteEtag,
  type ObjectStat,
} from '../utils/storage-fingerprint';
import {
  resolveS3Credentials,
  S3_PARTIAL_CREDENTIALS_MESSAGE,
} from '../utils/s3-credentials.util';
import { escapeRegExp } from '../../../utils/escape-regexp';

/** A folder outside every record's, where the conditional-write probe puts its random sentinels. */
const PROBE_FOLDER = 'paid-results-conditional-probe';

/**
 * Per (endpoint, bucket): the probe's answer, shared by concurrent callers, kept until `until` (ms since the epoch;
 * Infinity for a definitive answer about the store). A denied probe and one that could not tell are kept for a while
 * too, as "not supported", so the sweep and the purge take their safe path meanwhile and the probe is asked again after.
 */
const CONDITIONAL_MEMO = new Map<
  string,
  { answer: Promise<ConditionalCapabilities>; until: number }
>();
/** How long a denied probe (a 403 on its prefix) reads as "not supported" before it is asked again. */
const PROBE_DENIED_TTL_MS = 10 * 60 * 1000;
/** How long a probe that could not tell (after its retries) reads as "not supported" before it is asked again. */
const PROBE_UNSURE_TTL_MS = 30 * 1000;

/**
 * Implementation of StorageServiceInterface for Amazon S3
 * Handles file operations with Amazon S3 storage service
 */
class AmazonS3Adapter implements StorageServiceInterface {
  private readonly s3: S3Service;
  private readonly bucketName: string;
  private readonly region: string;
  private readonly usingIamRole: boolean;
  private readonly logger = Logger.getInstance({ service: 'AmazonS3Adapter' });

  constructor(credentials: {
    accessKeyId?: string;
    secretAccessKey?: string;
    region: string;
    bucket: string;
  }) {
    try {
      const { region, bucket } = credentials;

      // accessKeyId/secretAccessKey are optional: when omitted, the AWS SDK
      // falls back to its default credential provider chain (env vars,
      // shared config, ECS task role, EC2 instance metadata/IAM role).
      // region and bucket are always required to target the right resource.
      if (!region || !bucket) {
        throw new StorageConfigurationError(
          'Missing required S3 configuration',
          {
            missingFields: {
              region: !region,
              bucket: !bucket,
            },
          },
        );
      }

      // Validate and sanitize region input (defense-in-depth for AWS SDK v2 region validation advisory)
      const sanitizedRegion = this.validateAndSanitizeRegion(region);

      const resolvedCredentials = resolveS3Credentials(credentials);
      if (resolvedCredentials.kind === 'partial') {
        throw new StorageConfigurationError(S3_PARTIAL_CREDENTIALS_MESSAGE, {
          missingFields: { [resolvedCredentials.missingField]: true },
        });
      }
      this.usingIamRole = resolvedCredentials.kind === 'iamRole';

      // Retries (maxAttempts) and Signature V4 live in createS3Service.
      this.s3 = buildS3Service({
        region: sanitizedRegion,
        ...(resolvedCredentials.kind === 'explicit'
          ? {
              accessKeyId: resolvedCredentials.accessKeyId,
              secretAccessKey: resolvedCredentials.secretAccessKey,
            }
          : {}),
      });

      this.bucketName = bucket;
      this.region = sanitizedRegion;

      this.logger.debug(
        this.usingIamRole
          ? 'S3 adapter initialized using default AWS credential chain (IAM role)'
          : 'S3 adapter initialized using explicit AWS credentials',
        { bucket: this.bucketName, region: this.region },
      );
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

      if (result.Key === undefined || result.Key === '') {
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

      if (result.Key === undefined || result.Key === '') {
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

  async objectExistsAtPath(documentPath: string): Promise<boolean> {
    try {
      await this.s3
        .headObject({ Bucket: this.bucketName, Key: documentPath })
        .promise();
      return true;
    } catch (error) {
      const { code } = error as { code?: string };
      if (code === 'NotFound' || code === 'NoSuchKey') {
        return false;
      }
      throw error;
    }
  }

  async deleteObject(document: Document): Promise<void> {
    if (!document.s3?.url) {
      throw new StorageNotFoundError('Document S3 URL not found');
    }
    await this.s3
      .deleteObject({
        Bucket: this.bucketName,
        Key: this.extractKeyFromUrl(document.s3.url),
      })
      .promise();
  }

  async objectExists(document: Document): Promise<boolean> {
    if (!document.s3?.url) {
      // No stored URL means nothing can be checked, which is not the same as absent.
      throw new StorageNotFoundError('Document S3 URL not found');
    }
    try {
      await this.s3
        .headObject({
          Bucket: this.bucketName,
          Key: this.extractKeyFromUrl(document.s3.url),
        })
        .promise();
      return true;
    } catch (error) {
      // Only an object-level miss means absent; NoSuchBucket is also a 404 and
      // says the configuration is wrong, not that the file never arrived.
      const { code } = error as { code?: string };
      if (code === 'NotFound' || code === 'NoSuchKey') {
        return false;
      }
      throw error;
    }
  }

  /**
   * Size and ETag of the current object (HEAD). Null when the object or the stored URL is absent. A stored URL that cannot be
   * turned into a key rejects with `StorageUrlParseError`; any other failure rejects.
   */
  async statObject(document: Document): Promise<ObjectStat | null> {
    if (!document.s3?.url) return null;
    const key = this.extractKeyFromUrl(document.s3.url);
    try {
      const head = await this.s3
        .headObject({ Bucket: this.bucketName, Key: key })
        .promise();
      const etag = normalizeEtag(head.ETag);
      if (etag === '' || typeof head.ContentLength !== 'number')
        throw new StorageDownloadError(
          'Storage returned no size or ETag for the object',
        );
      return {
        size: head.ContentLength,
        etag,
        lastModified: head.LastModified
          ? new Date(head.LastModified).toISOString()
          : null,
      };
    } catch (error) {
      const { code } = error as { code?: string };
      if (code === 'NotFound' || code === 'NoSuchKey') return null;
      throw error;
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
        version === undefined
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
        data: response.Body,
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

  async signObjectUrl(
    storageKey: string,
    options: SignObjectUrlOptions,
  ): Promise<SignedObjectUrl> {
    try {
      if (options.method === 'PUT') {
        // Sent by the uploader, it becomes the stored object's Content-Type.
        const contentType = options.contentType ?? 'application/octet-stream';
        const url = await this.s3.getSignedUrlPromise('putObject', {
          Bucket: this.bucketName,
          Key: storageKey,
          ContentType: contentType,
          // Signed into the URL: S3 applies a conditional header only when it is a signed one, and the
          // uploader must then send the same value.
          ...(options.ifAbsent === true && { IfNoneMatch: '*' }),
          ...(options.ifMatch !== undefined && {
            IfMatch: quoteEtag(options.ifMatch),
          }),
          Expires: options.expiresInSeconds,
        });
        return {
          url,
          headers: {
            'Content-Type': contentType,
            ...(options.ifAbsent === true && { 'If-None-Match': '*' }),
            ...(options.ifMatch !== undefined && {
              'If-Match': quoteEtag(options.ifMatch),
            }),
          },
        };
      }
      const url = await this.s3.getSignedUrlPromise('getObject', {
        Bucket: this.bucketName,
        Key: storageKey,
        Expires: options.expiresInSeconds,
      });
      return { url, headers: {} };
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

      const params: {
        Bucket: string;
        Key: string;
        ResponseContentDisposition?: string;
      } = {
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
   * Lists all objects under a prefix, following pagination via ContinuationToken.
   * @param prefix - The S3 key prefix to list.
   * @returns A promise resolving to all matching S3 objects across all pages.
   */
  private async _listAllObjects(
    prefix: string,
  ): Promise<Array<{ Key?: string }>> {
    const allObjects: Array<{ Key?: string }> = [];
    let continuationToken: string | undefined;
    do {
      const params: {
        Bucket: string;
        Prefix: string;
        ContinuationToken?: string;
      } = {
        Bucket: this.bucketName,
        Prefix: prefix,
        ...(continuationToken && { ContinuationToken: continuationToken }),
      };
      const response = await this.s3.listObjectsV2(params).promise();
      if (response.Contents) {
        allObjects.push(...response.Contents);
      }
      continuationToken = response.IsTruncated ? response.NextContinuationToken : undefined;
    } while (continuationToken);
    return allObjects;
  }

  async listObjects(
    prefix: string,
    options: { after?: string; limit: number },
  ): Promise<ObjectPage> {
    // A continuation token is opaque base64 this service handed out: anything else (a key, a path) is the
    // caller's mistake and never reaches the store.
    if (
      options.after !== undefined &&
      !/^[A-Za-z0-9+/=_~-]{1,2048}$/.test(options.after)
    ) {
      throw new StorageValidationError('The listing cursor is not valid');
    }
    try {
      const response = await withDeadline(
        this.s3
          .listObjectsV2({
            Bucket: this.bucketName,
            Prefix: prefix,
            MaxKeys: options.limit,
            ...(options.after !== undefined && {
              ContinuationToken: options.after,
            }),
          })
          .promise(),
        this.callDeadlineMs,
        'listing',
      );
      return {
        objects: (response.Contents ?? []).map((o) => ({
          key: o.Key as string,
          size: o.Size ?? 0,
          lastModifiedMs: o.LastModified?.getTime() ?? 0,
          ...(o.ETag !== undefined && { etag: normalizeEtag(o.ETag) }),
        })),
        ...(response.IsTruncated &&
          response.NextContinuationToken && {
            next: response.NextContinuationToken,
          }),
      };
    } catch (error) {
      if (error instanceof StorageError) throw error;
      // A cursor this service did not issue: S3 rejects it with 400 InvalidArgument, MinIO with 501 or a 500
      // whose message names the token. The caller's mistake, not an outage.
      const {
        statusCode: status,
        code,
        message,
      } = error as {
        statusCode?: number;
        code?: string;
        message?: string;
      };
      if (
        options.after !== undefined &&
        (status === 400 ||
          status === 501 ||
          code === 'InvalidArgument' ||
          code === 'InvalidContinuationToken' ||
          code === 'NotImplemented' ||
          /continuation token|not implemented/i.test(message ?? ''))
      ) {
        throw new StorageValidationError('The listing cursor is not valid');
      }
      throw new StorageDownloadError('Failed to list objects in S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async deleteObjects(storageKeys: string[]): Promise<void> {
    try {
      for (let i = 0; i < storageKeys.length; i += 1000) {
        const result = await this.s3
          .deleteObjects({
            Bucket: this.bucketName,
            Delete: {
              Objects: storageKeys.slice(i, i + 1000).map((Key) => ({ Key })),
              Quiet: true,
            },
          })
          .promise();
        // DeleteObjects resolves even when individual keys fail.
        if (result.Errors && result.Errors.length > 0) {
          throw new StorageUploadError(
            'Failed to delete some objects from S3',
            {
              originalError: result.Errors.slice(0, 10)
                .map((e) => `${e.Key}: ${e.Code} ${e.Message ?? ''}`.trim())
                .join('; '),
            },
          );
        }
      }
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to delete objects from S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * `DeleteObjects` with a per-object `ETag`: the store removes an object only if its ETag still matches. An object
   * that changed answers `PreconditionFailed` (or `ConditionalRequestConflict` while a write of it is in flight) and
   * stays; one already gone (`NoSuchKey`) is a success. Any other per-key error rejects.
   */
  async deleteObjectsIfMatch(
    items: { key: string; etag: string }[],
  ): Promise<ConditionalDeleteResult> {
    const changed: string[] = [];
    try {
      for (let i = 0; i < items.length; i += 1000) {
        const batch = items.slice(i, i + 1000);
        const result = await withDeadline(
          this.s3
            .deleteObjects({
              Bucket: this.bucketName,
              Delete: {
                Objects: batch.map(({ key, etag }) => ({
                  Key: key,
                  ETag: quoteEtag(etag),
                })),
                Quiet: true,
              },
            })
            .promise(),
          this.callDeadlineMs,
          'delete by version',
        );
        const failures: string[] = [];
        for (const e of result.Errors ?? []) {
          if (
            e.Code === 'PreconditionFailed' ||
            e.Code === 'ConditionalRequestConflict'
          ) {
            if (e.Key !== undefined) changed.push(e.Key);
          } else if (e.Code !== 'NoSuchKey' && e.Code !== 'NotFound') {
            failures.push(`${e.Key}: ${e.Code} ${e.Message ?? ''}`.trim());
          }
        }
        if (failures.length > 0) {
          throw new StorageUploadError(
            'Failed to delete some objects from S3',
            { originalError: failures.slice(0, 10).join('; ') },
          );
        }
      }
      return { changed };
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to delete objects from S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * What this bucket honours, asked of the store itself once per (endpoint, bucket) and process. Only a definitive
   * answer is remembered; a timeout or a 5xx is not (this call falls back, the next asks again). See
   * `probeConditionalSupport`.
   */
  async conditionalCapabilities(): Promise<ConditionalCapabilities> {
    const memoKey = `${process.env.AWS_ENDPOINT_URL_S3 ?? ''}|${this.bucketName}`;
    // Never an error and never a wait without end: a probe that is denied or cannot tell answers "not supported" for a
    // while (the sweep and the purge then take their safe re-list path, as before conditional deletes existed) and is
    // asked again afterwards. Callers arriving meanwhile share the attempt in flight.
    const known = CONDITIONAL_MEMO.get(memoKey);
    if (known !== undefined && known.until > Date.now()) return known.answer;
    const holder: { answer: Promise<ConditionalCapabilities>; until: number } = {
      answer: Promise.resolve({ conditionalDelete: false, bump: false }),
      until: Number.POSITIVE_INFINITY,
    };
    holder.answer = this.resolveConditionalSupport().then((resolved) => {
      holder.until = resolved.ttlMs === Number.POSITIVE_INFINITY ? resolved.ttlMs : Date.now() + resolved.ttlMs;
      return resolved.capabilities;
    });
    CONDITIONAL_MEMO.set(memoKey, holder);
    return holder.answer;
  }

  private async resolveConditionalSupport(): Promise<{
    capabilities: ConditionalCapabilities;
    ttlMs: number;
  }> {
    let answer: ConditionalCapabilities | null = null;
    for (let attempt = 0; ; attempt += 1) {
      answer = await this.probeConditionalSupport();
      if (answer !== null) break;
      const delay = this.probeBackoffMs[attempt];
      if (delay === undefined) break;
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
    if (answer === null) {
      this.logger.warn(
        'The object store could not say whether it honours conditional paid-result writes; they are treated as not supported for now',
        { retryAfterMs: this.probeUnsureTtlMs },
      );
      return {
        capabilities: { conditionalDelete: false, bump: false },
        ttlMs: this.probeUnsureTtlMs,
      };
    }
    if (answer.denied === true) {
      return {
        capabilities: { conditionalDelete: false, bump: false },
        ttlMs: this.probeDeniedTtlMs,
      };
    }
    if (!answer.conditionalDelete || !answer.bump) {
      this.logger.warn(
        'The object store does not honour conditional paid-result writes; falling back to unconditional sweeps',
        { conditionalDelete: answer.conditionalDelete, bump: answer.bump },
      );
    }
    return { capabilities: answer, ttlMs: Number.POSITIVE_INFINITY };
  }

  /** Waits between the attempts of a probe that met a transient failure (tests shorten it). */
  probeBackoffMs: number[] = [250, 1000];

  /** The deadline of each storage call this adapter makes on the paid-result sweep path (tests shorten it). */
  callDeadlineMs = PAID_RESULT_CALL_DEADLINE_MS;

  /** How long a denied probe, and one that could not tell, read as "not supported" (tests shorten them). */
  probeDeniedTtlMs = PROBE_DENIED_TTL_MS;
  probeUnsureTtlMs = PROBE_UNSURE_TTL_MS;

  /** The deadline of the probe's conditional PUT (request and body). */
  probeTimeoutMs = 10_000;

  /**
   * Probe, on a random sentinel outside every record folder: (1) a delete with a wrong ETag must answer
   * precondition-failed and leave the object; (2) the right ETag must delete it, and a conditional delete of a
   * missing key is gone either way; (3) a PUT `If-Match` the right ETag with other bytes must succeed and change the
   * listed ETag, one with a wrong ETag must be refused. `null` = the probe was inconclusive (transient failure).
   */
  private async probeConditionalSupport(): Promise<ConditionalCapabilities | null> {
    const key = `${PROBE_FOLDER}/${randomUUID()}.json`;
    const status = (error: unknown): number | undefined =>
      (error as { $metadata?: { httpStatusCode?: number } }).$metadata
        ?.httpStatusCode ?? (error as { statusCode?: number }).statusCode;
    // Every SDK call of the probe has the probe's deadline: one that never answers must not hold the answer every caller
    // of this probe shares. The SDK wrapper takes no abort signal, so the call is raced and its late answer ignored.
    const bounded = async <T>(call: Promise<T>): Promise<T> => {
      let timer: NodeJS.Timeout | undefined;
      try {
        return await Promise.race([
          call,
          new Promise<never>((_resolve, reject) => {
            timer = setTimeout(
              () =>
                reject(
                  Object.assign(new Error('probe call timed out'), {
                    name: 'TimeoutError',
                  }),
                ),
              this.probeTimeoutMs,
            );
          }),
        ]);
      } finally {
        if (timer !== undefined) clearTimeout(timer);
        call.catch(() => undefined);
      }
    };
    // Until the first conditional request runs, a failure is the setup's (an unconditional upload), not evidence about the
    // store's conditions: it is no answer.
    const stage = { conditionalStarted: false };
    const exists = async (): Promise<string | null> => {
      try {
        const head = await bounded(
          this.s3.headObject({ Bucket: this.bucketName, Key: key }).promise(),
        );
        return normalizeEtag(head.ETag);
      } catch (error) {
        const code = (error as { code?: string }).code;
        if (
          code === 'NotFound' ||
          code === 'NoSuchKey' ||
          status(error) === 404
        )
          return null;
        throw error;
      }
    };
    // A refusal of the request itself (not of the condition) is no answer about the store: it is asked again later.
    const transient = new Error('transient');
    const isTransientStatus = (n: number): boolean =>
      n === 429 || n === 408 || (n >= 500 && n !== 501);
    const isTransientCode = (c: string | undefined): boolean =>
      c !== undefined &&
      /^(InternalError|SlowDown|ServiceUnavailable|RequestTimeout|Throttl|RequestLimitExceeded|ThrottlingException)/.test(
        c,
      );
    const delOnce = async (etag: string): Promise<string> => {
      stage.conditionalStarted = true;
      const result = await bounded(
        this.s3
          .deleteObjects({
            Bucket: this.bucketName,
            Delete: {
              Objects: [{ Key: key, ETag: quoteEtag(etag) }],
              Quiet: true,
            },
          })
          .promise(),
      );
      const code = result.Errors?.[0]?.Code;
      if (isTransientCode(code)) throw transient;
      return code ?? 'OK';
    };
    const putIfMatch = async (etag: string, body: string): Promise<number> => {
      const signed = await this.signObjectUrl(key, {
        method: 'PUT',
        contentType: 'application/json',
        expiresInSeconds: 60,
        ifMatch: etag,
      });
      stage.conditionalStarted = true;
      // One deadline over the request and its body: a stalled probe must not hold every caller that shares it.
      const signal = AbortSignal.timeout(this.probeTimeoutMs);
      const response = await fetch(signed.url, {
        method: 'PUT',
        headers: signed.headers,
        body,
        signal,
      });
      await response.arrayBuffer();
      if (isTransientStatus(response.status)) throw transient;
      return response.status;
    };
    try {
      await bounded(
        this.s3
          .upload({
            Bucket: this.bucketName,
            Key: key,
            Body: Buffer.from('{"probe":1}'),
            ContentType: 'application/json',
          })
          .promise(),
      );
      const e1 = await exists();
      if (e1 === null) return null;
      // (3) first, so a store that ignores the delete condition (and removes the sentinel in step 1) cannot hide that
      // it honours the write condition: PUT If-Match, wrong ETag refused, right one replaces and the ETag moves.
      const putWrong = await putIfMatch('0'.repeat(32), '{"probe":2}');
      const putRight = await putIfMatch(e1, '{"probe":3}');
      // Only a status that speaks of the condition is an answer: 412 (refused), 2xx (the condition was ignored or met) or
      // 501 (not implemented). A denial, a throttle or anything else says nothing about the store: ask again later.
      const speaksOfCondition = (n: number): boolean =>
        n === 412 || n === 501 || (n >= 200 && n < 300);
      if (!speaksOfCondition(putWrong) || !speaksOfCondition(putRight))
        return null;
      const e2 = await exists();
      const bump =
        putWrong === 412 &&
        putRight >= 200 &&
        putRight < 300 &&
        e2 !== null &&
        e2 !== e1;
      const current = e2 ?? e1;
      // (1) wrong ETag: refused, object kept.
      const wrong = await delOnce('0'.repeat(32));
      const keptAfterWrong = (await exists()) !== null;
      const refusal = (c: string): boolean =>
        c === 'PreconditionFailed' || c === 'ConditionalRequestConflict';
      const deleteRefuses = keptAfterWrong && refusal(wrong);
      if (!keptAfterWrong) {
        // The store deleted on a wrong ETag: it ignores the condition. Nothing more to learn about deletes.
        return { conditionalDelete: false, bump };
      }
      // The object stayed, but not because the store refused the condition (AccessDenied, a throttle, a code of its own):
      // that is no evidence about conditional deletes. Not remembered.
      if (wrong !== 'OK' && !refusal(wrong) && wrong !== 'NotImplemented')
        return null;
      // (2) the current ETag deletes; the missing key then answers gone (OK or NoSuchKey).
      const right = await delOnce(current);
      if (right !== 'OK' && !refusal(right) && right !== 'NotImplemented')
        return null;
      const goneAfterRight = (await exists()) === null;
      const missing = await delOnce(current);
      // A missing key answers OK, NoSuchKey or NotFound (gone). A refusal of the condition on it (412) is a store that
      // does not treat it as gone: not supported. Any other answer (AccessDenied, a code of its own) is ambiguous, and the
      // probe has just seen a conditional delete work: it is no answer.
      if (
        !['OK', 'NoSuchKey', 'NotFound'].includes(missing) &&
        !refusal(missing)
      )
        return null;
      const missingOk =
        missing === 'OK' || missing === 'NoSuchKey' || missing === 'NotFound';
      return {
        conditionalDelete:
          deleteRefuses && right === 'OK' && goneAfterRight && missingOk,
        bump,
      };
    } catch (error) {
      if (error === transient) return null;
      const name = (error as { name?: string }).name;
      if (name === 'TimeoutError' || name === 'AbortError') return null;
      const httpStatus = status(error);
      const errorCode = (error as { code?: string }).code ?? '';
      // A denial is logged wherever it happens, the sentinel upload included: it is the operator's one signal that the
      // probe prefix must be allowed, and it is not remembered, so it is asked (and logged) again on the next call.
      if (httpStatus === 403 || errorCode === 'AccessDenied') {
        // A fixed policy, not a blip: "not supported" for a while, so the sweep and the purge keep working on their safe
        // path instead of waiting on a probe that will keep being denied. Logged once per window (the answer is kept).
        this.logger.warn(
          'The object store denied the conditional-write probe; conditional paid-result writes are treated as not supported until it is asked again. Allow PutObject, GetObject and DeleteObject on the probe prefix to enable them',
          { probePrefix: PROBE_FOLDER, retryAfterMs: this.probeDeniedTtlMs },
        );
        return { conditionalDelete: false, bump: false, denied: true };
      }
      // A failure before any conditional request (the sentinel upload, a 501 on it included) says nothing about the
      // store's conditions.
      if (!stage.conditionalStarted) return null;
      if (httpStatus !== undefined && isTransientStatus(httpStatus))
        return null;
      // A 400 that is S3's own transient answer (RequestTimeout and its kin, which the SDK has already retried) says
      // nothing about the store's conditions.
      if (isTransientCode((error as { code?: string }).code)) return null;
      // Only a refusal that says "the conditional form is not supported" is an answer (501, or a 400 whose code names the
      // unsupported feature). A 403 (a denied probe prefix, a refreshing credential) and a 400 with any other code
      // (RequestTimeout among them) are not: they are asked again, and a sweep in that window fails and is retried instead
      // of deleting without a version. The warning names what to allow.
      if (
        httpStatus === 501 ||
        (httpStatus === 400 &&
          /NotImplemented|InvalidRequest|UnsupportedOperation|InvalidArgument/.test(
            errorCode,
          ))
      ) {
        return { conditionalDelete: false, bump: false };
      }
      return null;
    } finally {
      // Bounded: a cleanup that never answers must not hold the answer every caller of this probe shares. A sentinel
      // left behind is in a folder no listing shows.
      let timer: NodeJS.Timeout | undefined;
      await Promise.race([
        this.s3
          .deleteObjects({
            Bucket: this.bucketName,
            Delete: { Objects: [{ Key: key }], Quiet: true },
          })
          .promise()
          .catch(() => undefined),
        new Promise<void>((resolve) => {
          timer = setTimeout(resolve, this.probeTimeoutMs);
          timer.unref();
        }),
      ]);
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  async deleteTree(storagePath: string): Promise<StorageServiceResponse<void>> {
    try {
      // List all objects with this prefix (across all pages) then delete them in batches
      const prefix = storagePath.endsWith('/') ? storagePath : `${storagePath}/`;
      const allObjects = await this._listAllObjects(prefix);

      // Batch delete in chunks of 1000 (S3 DeleteObjects API limit)
      for (let i = 0; i < allObjects.length; i += 1000) {
        const batch = allObjects.slice(i, i + 1000);
        const result = await this.s3
          .deleteObjects({
            Bucket: this.bucketName,
            Delete: { Objects: batch.map((o) => ({ Key: o.Key! })) },
          })
          .promise();
        // DeleteObjects resolves even when individual keys fail (e.g. AccessDenied).
        if (result.Errors && result.Errors.length > 0) {
          throw new StorageUploadError('Failed to delete some objects from S3', {
            originalError: result.Errors.slice(0, 10)
              .map((e) => `${e.Key}: ${e.Code} ${e.Message ?? ''}`.trim())
              .join('; '),
          });
        }
      }

      // Also try to delete the exact key in case storagePath itself is a file
      try {
        await this.s3.deleteObject({ Bucket: this.bucketName, Key: storagePath }).promise();
      } catch {
        // ignore; the key may not exist as a standalone object
      }

      this.logger.info('S3 delete successful', { path: storagePath });
      return { statusCode: 200, data: undefined };
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to delete object from S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async copyObject(
    sourcePath: string,
    destinationPath: string,
  ): Promise<StorageServiceResponse<string>> {
    try {
      const copySource = this.encodeCopySource(sourcePath);
      await this.s3
        .copyObject({ Bucket: this.bucketName, CopySource: copySource, Key: destinationPath })
        .promise();
      const destUrl = this.getS3Url(destinationPath);
      this.logger.info('S3 copy successful', { src: sourcePath, dst: destinationPath });
      return { statusCode: 200, data: destUrl };
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to copy object in S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Recursively copies all objects under sourcePrefix to destinationPrefix.
   * An empty source prefix (zero objects) is a no-op success, not an error.
   * @param sourcePrefix - The source path / key prefix.
   * @param destinationPrefix - The destination path / key prefix.
   */
  async copyTree(
    sourcePrefix: string,
    destinationPrefix: string,
  ): Promise<StorageServiceResponse<void>> {
    try {
      const srcPrefix = sourcePrefix.endsWith('/') ? sourcePrefix : `${sourcePrefix}/`;
      const dstPrefix = destinationPrefix.endsWith('/')
        ? destinationPrefix
        : `${destinationPrefix}/`;
      const allObjects = await this._listAllObjects(srcPrefix);

      for (const obj of allObjects) {
        if (!obj.Key) continue;
        const relativePath = obj.Key.substring(srcPrefix.length);
        const destKey = `${dstPrefix}${relativePath}`;
        await this.s3
          .copyObject({
            Bucket: this.bucketName,
            CopySource: this.encodeCopySource(obj.Key),
            Key: destKey,
          })
          .promise();
      }

      this.logger.info('S3 tree copy successful', { src: sourcePrefix, dst: destinationPrefix });
      return { statusCode: 200, data: undefined };
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to copy tree in S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async renameObject(
    sourcePath: string,
    destinationPath: string,
  ): Promise<StorageServiceResponse<string>> {
    try {
      const copySource = this.encodeCopySource(sourcePath);
      await this.s3
        .copyObject({ Bucket: this.bucketName, CopySource: copySource, Key: destinationPath })
        .promise();
      await this.s3.deleteObject({ Bucket: this.bucketName, Key: sourcePath }).promise();
      const destUrl = this.getS3Url(destinationPath);
      this.logger.info('S3 rename successful', { src: sourcePath, dst: destinationPath });
      return { statusCode: 200, data: destUrl };
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to rename object in S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async renameTree(
    sourcePrefix: string,
    destinationPrefix: string,
  ): Promise<StorageServiceResponse<void>> {
    try {
      await this.copyTree(sourcePrefix, destinationPrefix);
      await this.deleteTree(sourcePrefix);
      this.logger.info('S3 tree rename successful', { src: sourcePrefix, dst: destinationPrefix });
      return { statusCode: 200, data: undefined };
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageUploadError('Failed to rename tree in S3', {
        originalError: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Validates and sanitizes AWS region input to prevent invalid region values.
   * Implements defense-in-depth for AWS SDK v2 region validation advisory.
   * @param region - The AWS region string to validate
   * @returns The sanitized region string
   * @throws {StorageConfigurationError} If the region format is invalid
   */
  private validateAndSanitizeRegion(region: string): string {
    // Trim whitespace
    const trimmed = region.trim();

    // Validate AWS region format using a regex.
    const awsRegionPattern = /^[a-z]{2,4}(-[a-z0-9]+)+-[0-9]+$/;

    if (!awsRegionPattern.test(trimmed.toLowerCase())) {
      throw new StorageConfigurationError(
        'Invalid AWS region format. Region must follow pattern: {area}-{location}-{number} (e.g., us-east-1)',
        {
          providedRegion: region,
          validationError: 'Region format validation failed',
        },
      );
    }

    return trimmed.toLowerCase();
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
      const urlPattern = new RegExp(
        `^https?://${escapeRegExp(this.bucketName)}\\.s3\\.(?:${escapeRegExp(this.region)}\\.)?amazonaws\\.com/(.+)`,
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
      throw new StorageUrlParseError('Invalid S3 URL format', {
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
  getObjectUrl(storageKey: string): string {
    return this.getS3Url(storageKey);
  }

  private getS3Url(key: string): string {
    // Ensure the key is URL-encoded (except for forward slashes)
    const encodedKey = key
      .split('/')
      .map((part) => encodeURIComponent(part))
      .join('/');
    return `https://${this.bucketName}.s3.${this.region}.amazonaws.com/${encodedKey}`;
  }

  /**
   * Builds a correctly percent-encoded CopySource value for S3's copyObject
   * API. CopySource is sent as the raw `x-amz-copy-source` HTTP header --
   * unlike `Key`, aws-sdk does NOT auto-encode it, so any special character
   * in the source key (spaces, em-dashes, commas, non-ASCII) that isn't
   * valid in a raw header value causes the request to be rejected outright.
   */
  private encodeCopySource(key: string): string {
    const encodedKey = key
      .split('/')
      .map((segment) => encodeURIComponent(segment))
      .join('/');
    return `${this.bucketName}/${encodedKey}`;
  }
}

// Own function so the export is a writable property. A re-export is a getter,
// and sinon cannot replace that for the health check's cleanup client.
export function createS3Service(config: S3ServiceConfig): S3Service {
  return buildS3Service(config);
}

export default AmazonS3Adapter;
