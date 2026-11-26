/**
 * QAFINAL-FU-VOL `key-rotation` (frontend/tests/e2e/perf/volume-budgets.json, `stores`): a whole rotate (survey,
 * apply, survey) over many records reads each record at most once per pass and writes each moved record at most once.
 * Every store is counted at its port, so a step that re-reads a record, or lists an org's records once per record,
 * shows here before it shows on a real deployment's configuration store.
 */
import { expect } from 'chai';
import { randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { InMemoryDataKeyStore } from '../../../src/modules/agent-harness/adapters/in-memory-data-key-store';
import {
  HkdfKeyEncryptionKey,
  RingKeyEncryptionKey,
} from '../../../src/modules/agent-harness/adapters/crypto/hkdf-key-encryption-key';
import { OrgDataKeys } from '../../../src/modules/agent-harness/application/org-data-keys';
import { SecretKeyRotation } from '../../../src/modules/agent-harness/application/secret-key-rotation';
import {
  ConfigStoreReencryptStep,
  DataKeyRewrapStep,
  OAuthClientSecretStep,
  type ClientSecretStore,
  type ForOrg,
  type RotationKeyValueStore,
} from '../../../src/modules/agent-harness/application/secret-key-rotation-steps';
import type { LeaderLease } from '../../../src/modules/agent-harness/ports/leader-lease';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { hashedSecret } from '../../../src/libs/encryptor/secret-key-ring';

const BUDGET = (
  JSON.parse(
    readFileSync(join(__dirname, '../../../../../../frontend/tests/e2e/perf/volume-budgets.json'), 'utf8'),
  ) as { stores: { 'key-rotation': { readsPerRecordPerPass: number; writesPerMovedRecord: number } } }
).stores['key-rotation'];

/** survey, apply, survey */
const PASSES = 3;
const CONFIG_KEYS = 300;
const PLAIN_EVERY = 10;
const APP_ORGS = 3;
const APPS_PER_ORG = 100;
const KEY_ORGS = 50;

const secret = (): string => randomBytes(32).toString('hex');
const kek = (raw: string): HkdfKeyEncryptionKey => new HkdfKeyEncryptionKey(() => Buffer.from(hashedSecret(raw), 'hex'));
const cipherOf = (...raws: string[]): EncryptionService =>
  EncryptionService.withRing('aes-256-gcm', raws.map(hashedSecret) as [string, ...string[]]);
const sameOrg: ForOrg = (_orgId, fn) => fn();

class Counts {
  readonly of = new Map<string, number>();
  bump(name: string): void {
    this.of.set(name, (this.of.get(name) ?? 0) + 1);
  }
  get(name: string): number {
    return this.of.get(name) ?? 0;
  }
}

class CountingKv implements RotationKeyValueStore {
  readonly values = new Map<string, string>();
  constructor(private readonly counts: Counts) {}
  async getAllKeys(): Promise<string[]> {
    this.counts.bump('kv.getAllKeys');
    return [...this.values.keys()];
  }
  async get<T>(key: string): Promise<T | null> {
    this.counts.bump('kv.get');
    return (this.values.get(key) ?? null) as T | null;
  }
  async compareAndSet<T>(key: string, expected: T | null, next: T): Promise<boolean> {
    this.counts.bump('kv.compareAndSet');
    if ((this.values.get(key) ?? null) !== expected) return false;
    this.values.set(key, next as unknown as string);
    return true;
  }
}

class CountingApps implements ClientSecretStore {
  readonly apps = new Map<string, { orgId: string; secret: string }>();
  constructor(private readonly counts: Counts) {}
  async listClientSecrets(orgId: string): Promise<Array<{ id: string; clientSecretEncrypted: string }>> {
    this.counts.bump('apps.listClientSecrets');
    return [...this.apps.entries()]
      .filter(([, app]) => app.orgId === orgId)
      .map(([id, app]) => ({ id, clientSecretEncrypted: app.secret }));
  }
  async replaceClientSecret(orgId: string, id: string, expected: string, next: string): Promise<boolean> {
    this.counts.bump('apps.replaceClientSecret');
    const app = this.apps.get(id);
    if (app?.orgId !== orgId || app.secret !== expected) return false;
    app.secret = next;
    return true;
  }
}

class CountingDataKeys extends InMemoryDataKeyStore {
  counting = false;
  constructor(private readonly counts: Counts) {
    super();
  }
  override async orgIds(): Promise<string[]> {
    if (this.counting) this.counts.bump('keys.orgIds');
    return super.orgIds();
  }
  override async list(orgId: string) {
    if (this.counting) this.counts.bump('keys.list');
    return super.list(orgId);
  }
  override async replaceWrap(...args: Parameters<InMemoryDataKeyStore['replaceWrap']>): Promise<boolean> {
    if (this.counting) this.counts.bump('keys.replaceWrap');
    return super.replaceWrap(...args);
  }
}

class OneLease implements LeaderLease {
  async tryAcquire(): Promise<boolean> {
    return true;
  }
  async release(): Promise<void> {}
}

describe('QAFINAL-FU-VOL key-rotation: a whole rotate over many records', () => {
  it('VOL-3: each record read at most once a pass, each moved record written once', async () => {
    const counts = new Counts();
    const [oldKey, newKey] = [secret(), secret()];
    const old = cipherOf(oldKey);

    const kv = new CountingKv(counts);
    let sealedKeys = 0;
    for (let i = 0; i < CONFIG_KEYS; i += 1) {
      const key = `/services/k${String(i).padStart(4, '0')}`;
      if (i % PLAIN_EVERY === 0) {
        kv.values.set(key, `{"endpoint":"http://x/${String(i)}"}`);
      } else {
        kv.values.set(key, i % 2 === 0 ? old.encrypt(`v${String(i)}`) : JSON.stringify({ name: 'x', secret: old.encrypt(`s${String(i)}`) }));
        sealedKeys += 1;
      }
    }

    const apps = new CountingApps(counts);
    const appOrgs = Array.from({ length: APP_ORGS }, (_unused, o) => `app-org-${String(o)}`);
    for (const orgId of appOrgs) {
      for (let a = 0; a < APPS_PER_ORG; a += 1) apps.apps.set(`${orgId}/app-${String(a)}`, { orgId, secret: old.encrypt(`cs-${String(a)}`) });
    }

    const store = new CountingDataKeys(counts);
    for (let o = 0; o < KEY_ORGS; o += 1) {
      const keys = new OrgDataKeys({ store, kek: kek(oldKey) });
      await keys.active(`key-org-${String(o)}`);
    }
    const dataKeys = (await Promise.all((await store.orgIds()).map((orgId) => store.list(orgId)))).flat().length;
    store.counting = true;

    const cipher = cipherOf(newKey, oldKey);
    const result = await new SecretKeyRotation({
      steps: [
        new DataKeyRewrapStep(store, new RingKeyEncryptionKey(() => [kek(newKey), kek(oldKey)]), sameOrg),
        new ConfigStoreReencryptStep(kv, cipher),
        new OAuthClientSecretStep(apps, async () => appOrgs, cipher, sameOrg),
      ],
      ring: { other: 'previous' },
      lease: new OneLease(),
      holder: 'operator-1',
    }).run('rotate');

    expect(result.ok).to.equal(true);
    const moved = Object.fromEntries(result.steps.map((step) => [step.step, step.moved]));
    expect(moved).to.deep.equal({
      'data-keys': dataKeys,
      'config-store': sealedKeys,
      'oauth-client-secrets': APP_ORGS * APPS_PER_ORG,
    });

    const reads = BUDGET.readsPerRecordPerPass * PASSES;
    const writes = BUDGET.writesPerMovedRecord;
    const seen = JSON.stringify(Object.fromEntries(counts.of));
    expect(counts.get('kv.getAllKeys'), seen).to.equal(PASSES);
    expect(counts.get('kv.get'), seen).to.be.at.most(reads * CONFIG_KEYS);
    expect(counts.get('kv.compareAndSet'), seen).to.be.at.most(writes * sealedKeys);
    expect(counts.get('apps.listClientSecrets'), seen).to.be.at.most(reads * APP_ORGS);
    expect(counts.get('apps.replaceClientSecret'), seen).to.be.at.most(writes * APP_ORGS * APPS_PER_ORG);
    expect(counts.get('keys.orgIds'), seen).to.equal(PASSES);
    expect(counts.get('keys.list'), seen).to.be.at.most(reads * KEY_ORGS);
    expect(counts.get('keys.replaceWrap'), seen).to.be.at.most(writes * dataKeys);
  });
});
