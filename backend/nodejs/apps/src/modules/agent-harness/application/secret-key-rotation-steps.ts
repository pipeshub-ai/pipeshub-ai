import type { DataKeyStore, KeyEncryptionKeyRing } from '../ports/data-keys';
import {
  emptyReport,
  type RotationStep,
  type StepContext,
  type StepReport,
} from './secret-key-rotation';

/** Runs `fn` bound to `orgId` (`forEnumeratedOrg`): PostgreSQL's row policies need the org of the row. */
export type ForOrg = <T>(orgId: string, fn: () => Promise<T>) => Promise<T>;

/** The platform cipher with its key ring (`EncryptionService.getInstance`). */
export interface RingCipher {
  encrypt(text: string): string;
  /** Throws when no key of the ring opens it. */
  decryptDetailed(text: string): { plaintext: string; current: boolean };
}

/**
 * Rewraps every org data key held under the previous KEK. Journal content
 * never moves: it is sealed under the data keys, and only their wraps change.
 */
export class DataKeyRewrapStep implements RotationStep {
  readonly name = 'data-keys';

  constructor(
    private readonly store: DataKeyStore,
    private readonly kek: KeyEncryptionKeyRing,
    private readonly forOrg: ForOrg,
  ) {}

  async run(context: StepContext): Promise<StepReport> {
    const report = emptyReport(this.name);
    const current = this.kek.id;
    const ring = new Set(this.kek.ids);
    for (const orgId of await this.store.orgIds()) {
      context.checkpoint();
      await this.forOrg(orgId, async () => {
        for (const key of await this.store.list(orgId)) {
          report.total += 1;
          if (key.kekId === current) {
            report.current += 1;
            continue;
          }
          if (!ring.has(key.kekId)) {
            report.unreadable += 1;
            continue;
          }
          if (!context.apply) {
            report.previous += 1;
            continue;
          }
          context.checkpoint();
          const ref = { orgId, kid: key.kid, version: key.version };
          let dataKey: Buffer;
          try {
            dataKey = await this.kek.unwrap(key.wrapped, ref, key.kekId);
          } catch {
            report.unreadable += 1;
            continue;
          }
          const wrapped = await this.kek.wrap(dataKey, ref);
          dataKey.fill(0);
          // false: another run moved it first, or the org was shredded; either way nothing is left to do here.
          if (
            await this.store.replaceWrap(orgId, key.kid, key.kekId, {
              kekId: current,
              wrapped,
            })
          ) {
            report.moved += 1;
          }
          report.current += 1;
        }
      });
      context.progress(report);
    }
    return report;
  }
}

const CIPHER = /^[0-9a-f]{24}:[0-9a-f]*:[0-9a-f]{32}$/;
const CAS_ATTEMPTS = 5;

export type RotationKeyValueStore = {
  getAllKeys(): Promise<string[]>;
  get<T>(key: string): Promise<T | null>;
  compareAndSet<T>(key: string, expected: T | null, next: T): Promise<boolean>;
};

interface Walked {
  value: string;
  changed: boolean;
  sealed: number;
  previous: number;
  unreadable: number;
}

/**
 * Re-encrypts the platform configuration store (Redis, etcd or PostgreSQL,
 * through `KeyValueStoreService`): a value that is one ciphertext (as Node
 * stores it, or JSON-quoted as Python does), or a plaintext JSON document
 * holding ciphertexts in its fields. A decrypted document is not searched
 * again: what it holds is under other keys (the credential vault's data keys).
 * Each write is a compare-and-set on the value read, so a concurrent edit wins
 * and is read again; the key's expiry is kept.
 */
export class ConfigStoreReencryptStep implements RotationStep {
  readonly name = 'config-store';

  constructor(
    private readonly kv: RotationKeyValueStore,
    private readonly cipher: RingCipher,
  ) {}

  async run(context: StepContext): Promise<StepReport> {
    const report = emptyReport(this.name);
    const keys = [...(await this.kv.getAllKeys())].sort();
    for (const [index, key] of keys.entries()) {
      context.checkpoint();
      await this.one(key, context, report);
      if ((index + 1) % 100 === 0) context.progress(report);
    }
    context.progress(report);
    return report;
  }

  private async one(
    key: string,
    context: StepContext,
    report: StepReport,
  ): Promise<void> {
    for (let attempt = 1; ; attempt += 1) {
      const raw = await this.kv.get<string>(key);
      if (typeof raw !== 'string') return;
      const walked = this.walk(raw);
      if (walked.sealed === 0) return;
      const counted = (): void => {
        report.total += 1;
        if (walked.unreadable > 0) report.unreadable += 1;
        else if (walked.previous > 0) report.previous += 1;
        else report.current += 1;
      };
      if (!context.apply || !walked.changed) {
        counted();
        return;
      }
      if (await this.kv.compareAndSet(key, raw, walked.value)) {
        report.total += 1;
        report.moved += 1;
        if (walked.unreadable > 0) report.unreadable += 1;
        else report.current += 1;
        return;
      }
      if (attempt === CAS_ATTEMPTS) {
        // Written to on every attempt: left as it is, and the final check counts it.
        counted();
        return;
      }
    }
  }

  private walk(raw: string): Walked {
    const sealed = this.reseal(raw);
    if (sealed !== null) return sealed;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return {
        value: raw,
        changed: false,
        sealed: 0,
        previous: 0,
        unreadable: 0,
      };
    }
    const totals = { sealed: 0, previous: 0, unreadable: 0, changed: false };
    const visit = (node: unknown): unknown => {
      if (typeof node === 'string') {
        const one = this.reseal(node);
        if (one === null) return node;
        totals.sealed += one.sealed;
        totals.previous += one.previous;
        totals.unreadable += one.unreadable;
        totals.changed ||= one.changed;
        return one.value;
      }
      if (Array.isArray(node)) return node.map(visit);
      if (node !== null && typeof node === 'object') {
        return Object.fromEntries(
          Object.entries(node as Record<string, unknown>).map(([k, v]) => [
            k,
            visit(v),
          ]),
        );
      }
      return node;
    };
    const next = visit(parsed);
    // Unchanged values keep their exact bytes; only a value that moved is written back.
    return { ...totals, value: totals.changed ? JSON.stringify(next) : raw };
  }

  /** One ciphertext, re-encrypted under the current key when the previous one opened it; null: not a ciphertext. */
  private reseal(text: string): Walked | null {
    if (!CIPHER.test(text)) return null;
    try {
      const opened = this.cipher.decryptDetailed(text);
      if (opened.current)
        return {
          value: text,
          changed: false,
          sealed: 1,
          previous: 0,
          unreadable: 0,
        };
      return {
        value: this.cipher.encrypt(opened.plaintext),
        changed: true,
        sealed: 1,
        previous: 1,
        unreadable: 0,
      };
    } catch {
      return {
        value: text,
        changed: false,
        sealed: 1,
        previous: 0,
        unreadable: 1,
      };
    }
  }
}

export interface ClientSecretStore {
  listClientSecrets(
    orgId: string,
  ): Promise<Array<{ id: string; clientSecretEncrypted: string }>>;
  replaceClientSecret(
    orgId: string,
    id: string,
    expected: string,
    next: string,
  ): Promise<boolean>;
}

/** Re-encrypts each OAuth app's client secret (the document store's `clientSecretEncrypted`). */
export class OAuthClientSecretStep implements RotationStep {
  readonly name = 'oauth-client-secrets';

  constructor(
    private readonly apps: ClientSecretStore,
    private readonly orgs: () => Promise<string[]>,
    private readonly cipher: RingCipher,
    private readonly forOrg: ForOrg,
  ) {}

  async run(context: StepContext): Promise<StepReport> {
    const report = emptyReport(this.name);
    for (const orgId of await this.orgs()) {
      context.checkpoint();
      await this.forOrg(orgId, async () => {
        for (const app of await this.apps.listClientSecrets(orgId)) {
          report.total += 1;
          let opened: { plaintext: string; current: boolean };
          try {
            opened = this.cipher.decryptDetailed(app.clientSecretEncrypted);
          } catch {
            report.unreadable += 1;
            continue;
          }
          if (opened.current) {
            report.current += 1;
            continue;
          }
          if (!context.apply) {
            report.previous += 1;
            continue;
          }
          context.checkpoint();
          const next = this.cipher.encrypt(opened.plaintext);
          if (
            await this.apps.replaceClientSecret(
              orgId,
              app.id,
              app.clientSecretEncrypted,
              next,
            )
          ) {
            report.moved += 1;
            report.current += 1;
          } else {
            // Changed since it was read (a new secret, or another run): the final check reads it again.
            report.previous += 1;
          }
        }
      });
      context.progress(report);
    }
    return report;
  }
}
