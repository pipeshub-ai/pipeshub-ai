import {
  CompleteMultipartUploadCommand,
  CopyObjectCommand,
  CreateMultipartUploadCommand,
  DeleteObjectCommand,
  DeleteObjectsCommand,
  GetObjectCommand,
  HeadObjectCommand,
  ListObjectsV2Command,
  PutObjectCommand,
  S3Client,
  UploadPartCommand,
  type GetObjectCommandOutput,
} from '@aws-sdk/client-s3';
import { Upload } from '@aws-sdk/lib-storage';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

/**
 * v2-shaped S3 client implemented with AWS SDK v3.
 * Call sites and unit tests keep using `.promise()` and `getSignedUrlPromise`.
 * v3 signs with Signature Version 4, which SSE-KMS buckets require.
 */
export interface S3Service {
  upload(params: {
    Bucket: string;
    Key: string;
    Body: Buffer;
    ContentType?: string;
  }): { promise: () => Promise<{ Key?: string }> };
  headObject(params: { Bucket: string; Key: string }): {
    promise: () => Promise<unknown>;
  };
  deleteObject(params: { Bucket: string; Key: string }): {
    promise: () => Promise<unknown>;
  };
  getObject(params: { Bucket: string; Key: string }): {
    promise: () => Promise<{ Body?: Buffer }>;
  };
  createMultipartUpload(params: {
    Bucket: string;
    Key: string;
    ContentType?: string;
  }): { promise: () => Promise<{ UploadId?: string }> };
  completeMultipartUpload(params: {
    Bucket: string;
    Key: string;
    UploadId: string;
    MultipartUpload: { Parts: Array<{ ETag: string; PartNumber: number }> };
  }): { promise: () => Promise<{ Key?: string }> };
  deleteObjects(params: {
    Bucket: string;
    Delete: { Objects: Array<{ Key: string }> };
  }): {
    promise: () => Promise<{
      Errors?: Array<{ Key?: string; Code?: string; Message?: string }>;
    }>;
  };
  listObjectsV2(params: {
    Bucket: string;
    Prefix: string;
    ContinuationToken?: string;
  }): {
    promise: () => Promise<{
      Contents?: Array<{ Key?: string }>;
      IsTruncated?: boolean;
      NextContinuationToken?: string;
    }>;
  };
  copyObject(params: { Bucket: string; CopySource: string; Key: string }): {
    promise: () => Promise<unknown>;
  };
  getSignedUrlPromise(
    operation: string,
    params: Record<string, unknown>,
  ): Promise<string>;
}

export interface S3ServiceConfig {
  region: string;
  accessKeyId?: string;
  secretAccessKey?: string;
  forcePathStyle?: boolean;
}

function annotateError(error: unknown): never {
  if (typeof error === 'object' && error !== null) {
    const record = error as {
      name?: string;
      code?: string;
      $metadata?: { httpStatusCode?: number };
    };
    if (record.code === undefined || record.code === '') {
      // A 404 is not always a missing object: `NoSuchBucket` read as
      // `NotFound` would let callers delete a document whose object is fine.
      if (record.name !== undefined && record.name !== '') {
        record.code = record.name;
      } else if (record.$metadata?.httpStatusCode === 404) {
        record.code = 'NotFound';
      }
    }
  }
  throw error;
}

async function send<T>(client: S3Client, command: object): Promise<T> {
  try {
    // S3Client.send overloads don't share a command parameter type.
    return (await client.send(command as never)) as T;
  } catch (error) {
    annotateError(error);
  }
}

function promised<T>(fn: () => Promise<T>): { promise: () => Promise<T> } {
  return {
    promise: async () => {
      try {
        return await fn();
      } catch (error) {
        annotateError(error);
      }
    },
  };
}

function nonEmpty(value: string | undefined): string | undefined {
  if (value === undefined || value === '') {
    return undefined;
  }
  return value;
}

export function createS3Service(config: S3ServiceConfig): S3Service {
  const endpoint = nonEmpty(process.env.AWS_ENDPOINT_URL_S3);
  const forcePathStyle =
    config.forcePathStyle === true ||
    process.env.AWS_S3_FORCE_PATH_STYLE === 'true';
  const accessKeyId = nonEmpty(config.accessKeyId);
  const secretAccessKey = nonEmpty(config.secretAccessKey);
  const client = new S3Client({
    region: config.region,
    endpoint,
    forcePathStyle,
    maxAttempts: 9,
    // The WHEN_SUPPORTED default signs a CRC32 of the empty presign body into
    // put/part URLs, so S3 rejects the real upload; GET URLs gain
    // x-amz-checksum-mode, which S3-compatible stores refuse.
    requestChecksumCalculation: 'WHEN_REQUIRED',
    responseChecksumValidation: 'WHEN_REQUIRED',
    credentials:
      accessKeyId !== undefined && secretAccessKey !== undefined
        ? { accessKeyId, secretAccessKey }
        : undefined,
  });

  return {
    upload(params) {
      return promised(async () => {
        const result = await new Upload({ client, params }).done();
        return { Key: result.Key };
      });
    },
    headObject(params) {
      return promised(() => send(client, new HeadObjectCommand(params)));
    },
    deleteObject(params) {
      return promised(() => send(client, new DeleteObjectCommand(params)));
    },
    getObject(params) {
      return promised(async () => {
        const response = await send<GetObjectCommandOutput>(
          client,
          new GetObjectCommand(params),
        );
        const body = response.Body;
        if (body && typeof body.transformToByteArray === 'function') {
          return { Body: Buffer.from(await body.transformToByteArray()) };
        }
        return { Body: undefined };
      });
    },
    createMultipartUpload(params) {
      return promised(() =>
        send(client, new CreateMultipartUploadCommand(params)),
      );
    },
    completeMultipartUpload(params) {
      return promised(() =>
        send(client, new CompleteMultipartUploadCommand(params)),
      );
    },
    deleteObjects(params) {
      return promised(() => send(client, new DeleteObjectsCommand(params)));
    },
    listObjectsV2(params) {
      return promised(() => send(client, new ListObjectsV2Command(params)));
    },
    copyObject(params) {
      return promised(() => send(client, new CopyObjectCommand(params)));
    },
    async getSignedUrlPromise(operation, params) {
      const expiresIn =
        typeof params.Expires === 'number' ? params.Expires : 3600;
      const { Expires: _expires, ...rest } = params;
      const input = rest as {
        Bucket: string;
        Key: string;
        PartNumber?: number;
        UploadId?: string;
        ResponseContentDisposition?: string;
        ContentType?: string;
      };
      let command: GetObjectCommand | PutObjectCommand | UploadPartCommand;
      if (operation === 'getObject') {
        command = new GetObjectCommand(input);
      } else if (operation === 'putObject') {
        command = new PutObjectCommand(input);
      } else if (operation === 'uploadPart') {
        if (
          typeof input.PartNumber !== 'number' ||
          input.UploadId === undefined ||
          input.UploadId === ''
        ) {
          throw new Error(
            'uploadPart presign requires PartNumber and UploadId',
          );
        }
        command = new UploadPartCommand({
          Bucket: input.Bucket,
          Key: input.Key,
          PartNumber: input.PartNumber,
          UploadId: input.UploadId,
        });
      } else {
        throw new Error(`Unsupported presign operation: ${operation}`);
      }
      try {
        return await getSignedUrl(client, command as never, { expiresIn });
      } catch (error) {
        annotateError(error);
      }
    },
  };
}
