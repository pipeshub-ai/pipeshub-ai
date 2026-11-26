import { isValidId, newObjectId } from '../../../../libs/repositories/ids';
import { toDriverId } from '../../../../libs/repositories/mongo';
import { StorageServiceInterface } from '../../../storage/services/storage.service';
import { Document } from '../../../storage/types/storage.service.types';
import { PayloadStoreUnavailableError } from '../../domain/errors';
import { PayloadStore } from '../../ports/payload-store';

export type StorageVendorKey = 'local' | 's3' | 'azureBlob';

const pathFor = (orgId: string, hash: string): string =>
  `agent-harness/journal/${orgId}/${hash.replace(/^sha256:/, '')}.bin`;

/**
 * Claim-check payloads through the platform blob storage (local, S3, Azure).
 * The ref is the storage URL; `get` refuses a ref outside the org's prefix.
 */
export class StoragePayloadStore implements PayloadStore {
  constructor(
    private readonly storage: () => Promise<StorageServiceInterface>,
    private readonly vendor: StorageVendorKey,
  ) {}

  async put(orgId: string, hash: string, bytes: Buffer): Promise<string> {
    let response: Awaited<
      ReturnType<StorageServiceInterface['uploadDocumentToStorageService']>
    >;
    try {
      response = await (
        await this.storage()
      ).uploadDocumentToStorageService({
        documentPath: pathFor(orgId, hash),
        buffer: bytes,
        mimeType: 'application/octet-stream',
        isVersioned: false,
      });
    } catch (error) {
      throw new PayloadStoreUnavailableError('put', error);
    }
    if (response.statusCode !== 200 || response.data === undefined) {
      throw new PayloadStoreUnavailableError(
        'put',
        new Error(`upload answered ${String(response.statusCode)}`),
      );
    }
    return response.data;
  }

  async get(orgId: string, ref: string): Promise<Buffer | null> {
    const prefix = `agent-harness/journal/${orgId}/`;
    const at = ref.indexOf(prefix);
    if (at < 0) return null;
    const documentPath = ref.slice(at);
    let storage: StorageServiceInterface;
    try {
      storage = await this.storage();
    } catch (error) {
      throw new PayloadStoreUnavailableError('get', error);
    }
    const document = {
      documentName: documentPath.split('/').pop() ?? documentPath,
      documentPath,
      isVersionedFile: false,
      orgId: toDriverId(isValidId(orgId) ? orgId : newObjectId()),
      initiatorUserId: null,
      extension: '.bin',
      currentVersion: 0,
      isDeleted: false,
      storageVendor: this.vendor,
      [this.vendor]: { url: ref },
    } as unknown as Document;
    try {
      const response = await storage.getBufferFromStorageService(document);
      return response.statusCode === 200 && response.data !== undefined
        ? response.data
        : null;
    } catch (error) {
      let exists: boolean | undefined;
      try {
        exists = await storage.objectExistsAtPath?.(documentPath);
      } catch {
        exists = undefined;
      }
      // Only a store that answered "absent" makes the payload missing (terminal).
      if (exists === false) return null;
      throw new PayloadStoreUnavailableError('get', error);
    }
  }
}
