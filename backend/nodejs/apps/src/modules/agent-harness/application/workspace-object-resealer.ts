import { sessionWorkspaceId, WorkspaceFileEntry } from '../domain/workspace';
import {
  decodeSealedId,
  encodeSealedId,
  openObject,
  SealingKey,
  sealObject,
} from '../domain/workspace-sealing';
import { WorkspaceObjectBytes } from '../ports/workspace-objects';
import { SessionWorkspaceKeys } from './session-workspace-keys';

/** Copies one manifest entry's object from one session's key to another's. */
export type ObjectReseal = (
  entry: WorkspaceFileEntry,
) => Promise<WorkspaceFileEntry>;

/** One manifest entry's plaintext. */
export type ObjectRead = (entry: WorkspaceFileEntry) => Promise<Buffer>;

/** A file's bytes sealed and stored, as the manifest entry that names them. */
export type ObjectWrite = (
  path: string,
  bytes: Buffer,
  mimeType: string,
) => Promise<WorkspaceFileEntry>;

/**
 * A fork's objects are copies (SS3): each is opened with the source
 * session's key and sealed again with the fork's, so shredding either key
 * leaves the other session's files readable. Keys never leave Node.
 */
export class WorkspaceObjectResealer {
  constructor(
    private readonly keys: Pick<SessionWorkspaceKeys, 'issue'>,
    private readonly objects: WorkspaceObjectBytes,
  ) {}

  /** Reads a session's objects with its key, for its owner's diff (SLASH2 `/diff`); the bytes stay in Node. */
  async reader(orgId: string, sessionId: string): Promise<ObjectRead> {
    const issued = await this.keys.issue(orgId, sessionId);
    const key: SealingKey = { keyId: issued.keyId, key: issued.key };
    const scope = { tenantId: orgId, partition: sessionWorkspaceId(sessionId) };
    return async (entry) =>
      openObject(
        scope,
        key,
        await this.objects.get(orgId, decodeSealedId(entry.objectId)),
        entry,
      );
  }

  /** Seals a person's file with the session's key and stores it (SLASH3 `/upload`); the bytes stay in Node. */
  async writer(orgId: string, sessionId: string): Promise<ObjectWrite> {
    const issued = await this.keys.issue(orgId, sessionId);
    const key: SealingKey = { keyId: issued.keyId, key: issued.key };
    const scope = { tenantId: orgId, partition: sessionWorkspaceId(sessionId) };
    return async (path, bytes, mimeType) => {
      const sealed = sealObject(scope, key, bytes);
      const inner = await this.objects.put(orgId, sealed.stored);
      return {
        path,
        objectId: encodeSealedId(inner),
        contentHash: sealed.contentHash,
        sizeBytes: sealed.sizeBytes,
        mimeType,
      };
    };
  }

  async between(
    orgId: string,
    fromSessionId: string,
    toSessionId: string,
  ): Promise<ObjectReseal> {
    const [from, to] = await Promise.all([
      this.keys.issue(orgId, fromSessionId),
      this.keys.issue(orgId, toSessionId),
    ]);
    const fromKey: SealingKey = { keyId: from.keyId, key: from.key };
    const toKey: SealingKey = { keyId: to.keyId, key: to.key };
    const fromScope = {
      tenantId: orgId,
      partition: sessionWorkspaceId(fromSessionId),
    };
    const toScope = {
      tenantId: orgId,
      partition: sessionWorkspaceId(toSessionId),
    };
    return async (entry) => {
      const stored = await this.objects.get(
        orgId,
        decodeSealedId(entry.objectId),
      );
      const plaintext = openObject(fromScope, fromKey, stored, entry);
      const sealed = sealObject(toScope, toKey, plaintext);
      const inner = await this.objects.put(orgId, sealed.stored);
      return {
        ...entry,
        objectId: encodeSealedId(inner),
        contentHash: sealed.contentHash,
        sizeBytes: sealed.sizeBytes,
      };
    };
  }
}
