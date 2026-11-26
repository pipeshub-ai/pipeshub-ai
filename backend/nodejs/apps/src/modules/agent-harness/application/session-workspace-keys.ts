import { randomBytes, randomUUID } from 'node:crypto';
import { DATA_KEY_BYTES } from '../domain/data-protection';
import { SealedContentError } from '../domain/errors';
import {
  sessionWorkspaceId,
  WorkspaceError,
  workspaceKeySealContext,
} from '../domain/workspace';
import {
  WorkspaceKeyRecord,
  WorkspaceKeyStore,
} from '../ports/workspace-store';
import { EnvelopeCipher } from './envelope-cipher';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

export interface IssuedWorkspaceKey {
  keyId: string;
  /** The workspace the key belongs to: every fork of the session uses it. */
  keyDomain: string;
  key: Buffer;
}

const destroyed = (): WorkspaceError =>
  new WorkspaceError(
    'WORKSPACE_KEY_DESTROYED',
    'The workspace key of this session was destroyed',
  );

export interface SessionWorkspaceKeysOptions {
  store: WorkspaceKeyStore;
  cipher: EnvelopeCipher;
  logger?: HarnessLogger;
  now?: () => number;
  newKeyId?: () => string;
  newKey?: () => Buffer;
}

/**
 * One data key per session workspace (D33). Node creates it, stores it only
 * wrapped with the org's data key, and hands the unwrapped key to the
 * worker that holds a lease on a run of the session. Destroying it
 * (crypto-shred) makes every object of the session unreadable and is final:
 * no new key is issued for that session. An org shred reaches it too, since
 * its wrapping key goes.
 */
export class SessionWorkspaceKeys {
  private readonly store: WorkspaceKeyStore;
  private readonly cipher: EnvelopeCipher;
  private readonly logger: HarnessLogger;
  private readonly now: () => number;
  private readonly newKeyId: () => string;
  private readonly newKey: () => Buffer;

  constructor(options: SessionWorkspaceKeysOptions) {
    this.store = options.store;
    this.cipher = options.cipher;
    this.logger = options.logger ?? silentHarnessLogger;
    this.now = options.now ?? Date.now;
    this.newKeyId = options.newKeyId ?? randomUUID;
    this.newKey = options.newKey ?? (() => randomBytes(DATA_KEY_BYTES));
  }

  async issue(orgId: string, sessionId: string): Promise<IssuedWorkspaceKey> {
    const record =
      (await this.store.get(orgId, sessionId)) ??
      (await this.create(orgId, sessionId));
    if (record.wrapped === null) throw destroyed();
    let key: Buffer;
    try {
      key = await this.cipher.open(
        orgId,
        'workspace.key',
        workspaceKeySealContext(sessionId, record.keyId),
        record.wrapped,
      );
    } catch (error) {
      if (error instanceof SealedContentError && error.reason === 'shredded') {
        throw destroyed();
      }
      throw error;
    }
    return {
      keyId: record.keyId,
      keyDomain: sessionWorkspaceId(sessionId),
      key,
    };
  }

  async shred(orgId: string, sessionId: string): Promise<boolean> {
    const dropped = await this.store.destroy(orgId, sessionId, this.now());
    this.logger.info(
      'workspace.key_shredded',
      harnessLogMeta('workspace.key_shredded', { orgId, sessionId, dropped }),
    );
    return dropped;
  }

  private async create(
    orgId: string,
    sessionId: string,
  ): Promise<WorkspaceKeyRecord> {
    const keyId = this.newKeyId();
    const record: WorkspaceKeyRecord = {
      orgId,
      sessionId,
      keyId,
      wrapped: await this.cipher.seal(
        orgId,
        'workspace.key',
        workspaceKeySealContext(sessionId, keyId),
        this.newKey(),
      ),
      createdAt: this.now(),
      destroyedAt: null,
    };
    if (await this.store.insert(record)) {
      this.logger.info(
        'workspace.key_created',
        harnessLogMeta('workspace.key_created', { orgId, sessionId, keyId }),
      );
      return record;
    }
    // A concurrent issuer won, or the session was shredded; read what is there.
    const winner = await this.store.get(orgId, sessionId);
    if (winner === null)
      throw new Error('A workspace key insert lost to nothing');
    return winner;
  }
}
