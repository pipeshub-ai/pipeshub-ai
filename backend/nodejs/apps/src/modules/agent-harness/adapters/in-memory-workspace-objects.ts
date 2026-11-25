import { createHash } from 'node:crypto';
import type { InnerObjectRef } from '../domain/workspace-sealing';
import type { WorkspaceObjectBytes } from '../ports/workspace-objects';

const sha256 = (bytes: Buffer): string =>
  `sha256:${createHash('sha256').update(bytes).digest('hex')}`;

/** Content-addressed bytes per org, as the worker's in-memory object store keeps them. */
export class InMemoryWorkspaceObjects implements WorkspaceObjectBytes {
  readonly objects = new Map<string, Buffer>();

  get(orgId: string, ref: InnerObjectRef): Promise<Buffer> {
    const bytes = this.objects.get(`${orgId}\u0000${ref.objectId}`);
    if (bytes === undefined) {
      return Promise.reject(new Error('Workspace object not found'));
    }
    if (bytes.length !== ref.sizeBytes || sha256(bytes) !== ref.contentHash) {
      return Promise.reject(
        new Error('Workspace object does not match its reference'),
      );
    }
    return Promise.resolve(Buffer.from(bytes));
  }

  put(orgId: string, bytes: Buffer): Promise<InnerObjectRef> {
    const hash = sha256(bytes);
    this.objects.set(`${orgId}\u0000${hash}`, Buffer.from(bytes));
    return Promise.resolve({
      objectId: hash,
      contentHash: hash,
      sizeBytes: bytes.length,
    });
  }
}
