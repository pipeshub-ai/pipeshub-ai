import axios, { AxiosInstance } from 'axios';
import FormData from 'form-data';
import { createHash } from 'node:crypto';
import type { InnerObjectRef } from '../../domain/workspace-sealing';
import type { WorkspaceObjectBytes } from '../../ports/workspace-objects';

export interface StorageEndpoint {
  /** The storage service's base URL (Node's own storage routes). */
  baseUrl: string;
  /** `local`, `s3` or `azureBlob`: local stores in one upload, the others through a signed URL. */
  storageType: string;
}

const INTERNAL = '/api/v1/document/internal';
const MIME = 'application/octet-stream';

const sha256Hex = (bytes: Buffer): string =>
  createHash('sha256').update(bytes).digest('hex');

/** The storage service answered something that is not a stored object. */
export class WorkspaceObjectStorageError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WorkspaceObjectStorageError';
  }
}

// The storage service sends the raw bytes (main #4398); a service from before
// that sent a JSON-serialized Buffer, still accepted while a rollout mixes
// versions.
function bytesOf(payload: unknown, contentType: string): Buffer {
  if (!contentType.toLowerCase().includes('application/json')) {
    if (Buffer.isBuffer(payload)) return payload;
    if (payload instanceof ArrayBuffer) return Buffer.from(payload);
    throw new WorkspaceObjectStorageError('Storage returned no object bytes');
  }
  const parsed: unknown = Buffer.isBuffer(payload)
    ? JSON.parse(payload.toString('utf8'))
    : payload instanceof ArrayBuffer
      ? JSON.parse(Buffer.from(payload).toString('utf8'))
      : payload;
  const data =
    parsed !== null &&
    typeof parsed === 'object' &&
    (parsed as { type?: unknown }).type === 'Buffer'
      ? (parsed as { data?: unknown }).data
      : parsed;
  if (
    !Array.isArray(data) ||
    data.some(
      (value) =>
        typeof value !== 'number' ||
        !Number.isInteger(value) ||
        value < 0 ||
        value > 255,
    )
  ) {
    throw new WorkspaceObjectStorageError('Storage returned no object bytes');
  }
  return Buffer.from(data as number[]);
}

const documentIdOf = (payload: unknown): string => {
  const id = (payload as { _id?: unknown } | null)?._id;
  if (typeof id !== 'string' || id.length === 0) {
    throw new WorkspaceObjectStorageError(
      'Storage stored the object without an id',
    );
  }
  return id;
};

/**
 * The workers' object store as Node reaches it (SS3): the storage service's
 * internal routes with an org-scoped storage token, written and read the way
 * `PipesHubBinaryObjectStore` does (one document per object, the bytes
 * checked against the ref on read), so a fork's copies are found by the
 * fork's worker.
 */
export class StorageWorkspaceObjects implements WorkspaceObjectBytes {
  constructor(
    private readonly endpoint: () => Promise<StorageEndpoint> | StorageEndpoint,
    private readonly storageToken: (orgId: string) => string,
    private readonly http: AxiosInstance = axios,
  ) {}

  async get(orgId: string, ref: InnerObjectRef): Promise<Buffer> {
    const { baseUrl } = await this.endpoint();
    const response = await this.http.get<unknown>(
      `${baseUrl}${INTERNAL}/${encodeURIComponent(ref.objectId)}/buffer`,
      {
        headers: { Authorization: `Bearer ${this.storageToken(orgId)}` },
        responseType: 'arraybuffer',
      },
    );
    const bytes = bytesOf(
      response.data,
      String(response.headers['content-type'] ?? ''),
    );
    if (
      bytes.length !== ref.sizeBytes ||
      `sha256:${sha256Hex(bytes)}` !== ref.contentHash
    ) {
      throw new WorkspaceObjectStorageError(
        'Stored object does not match its reference',
      );
    }
    return bytes;
  }

  async put(orgId: string, bytes: Buffer): Promise<InnerObjectRef> {
    const { baseUrl, storageType } = await this.endpoint();
    const digest = sha256Hex(bytes);
    const recordId = `agent-harness-object-${digest}`;
    const headers = { Authorization: `Bearer ${this.storageToken(orgId)}` };
    const described = {
      documentName: digest,
      documentPath: `attachments/${recordId}`,
      extension: 'bin',
      recordId,
    };
    let documentId: string;
    if (storageType === 'local') {
      const form = new FormData();
      form.append('file', bytes, {
        filename: `${digest}.bin`,
        contentType: MIME,
      });
      for (const [field, value] of Object.entries(described)) {
        form.append(field, value);
      }
      form.append('isVersionedFile', 'false');
      const response = await this.http.post<unknown>(
        `${baseUrl}${INTERNAL}/upload`,
        form,
        { headers: { ...headers, ...form.getHeaders() } },
      );
      documentId = documentIdOf(response.data);
    } else {
      const placeholder = await this.http.post<unknown>(
        `${baseUrl}${INTERNAL}/placeholder`,
        { ...described, isVersionedFile: false },
        { headers },
      );
      documentId = documentIdOf(placeholder.data);
      const signed = await this.http.post<{ signedUrl?: unknown }>(
        `${baseUrl}${INTERNAL}/${encodeURIComponent(documentId)}/directUpload`,
        {},
        { headers },
      );
      const signedUrl = signed.data.signedUrl;
      if (typeof signedUrl !== 'string' || signedUrl.length === 0) {
        throw new WorkspaceObjectStorageError('Storage gave no upload URL');
      }
      await this.http.put(signedUrl, bytes, {
        headers: { 'Content-Type': MIME },
        maxBodyLength: Infinity,
      });
    }
    return {
      objectId: documentId,
      contentHash: `sha256:${digest}`,
      sizeBytes: bytes.length,
    };
  }
}

/**
 * Deletes a stored object for good through the storage service's purge route
 * (every stored copy, then its document); one already gone counts as deleted.
 * OWD-12: the retention sweeper deletes expired sandbox snapshots with it.
 */
export class StorageObjectPurger {
  constructor(
    private readonly endpoint: () => Promise<StorageEndpoint> | StorageEndpoint,
    private readonly storageToken: (orgId: string) => string,
    private readonly http: AxiosInstance = axios,
  ) {}

  async purge(orgId: string, objectId: string): Promise<void> {
    const { baseUrl } = await this.endpoint();
    await this.http.delete(
      `${baseUrl}${INTERNAL}/${encodeURIComponent(objectId)}/purge`,
      { headers: { Authorization: `Bearer ${this.storageToken(orgId)}` } },
    );
  }
}
