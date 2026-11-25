import type { InnerObjectRef } from '../domain/workspace-sealing';

/**
 * The store under the seal (the worker's `BinaryObjectStore`, PipesHub
 * storage in production): opaque bytes by reference, scoped to the org. Node
 * reads and writes it only to copy a session's objects for a fork (SS3); the
 * bytes it handles are always sealed.
 */
export interface WorkspaceObjectBytes {
  /** The stored bytes; rejects when their size or hash is not the ref's. */
  get(orgId: string, ref: InnerObjectRef): Promise<Buffer>;
  put(orgId: string, bytes: Buffer): Promise<InnerObjectRef>;
}
