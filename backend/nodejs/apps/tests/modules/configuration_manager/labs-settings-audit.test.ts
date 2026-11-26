/**
 * CPD-1 CW-9: a Labs save (UX2's compare-and-set save, unchanged) is one
 * audited control-plane write. A save that lands is one config.changed naming
 * what it decided, by path; a save UX2 refuses (`labs_changed`) or that loses
 * every compare-and-set round records nothing; the entry never carries values.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as cmConfig from '../../../src/modules/configuration_manager/config/config';
import * as encryptorModule from '../../../src/libs/encryptor/encryptor';
import { setPlatformSettings } from '../../../src/modules/configuration_manager/controller/cm_controller';
import { auditedControlPlane } from '../control-plane/support/in-memory';
import { inRequestOrg } from '../../helpers/request-org-context';

const ORG = 'org-labs';

/** The settings key with an atomic compare-and-set; `interleave` writes between a read and its CAS. */
class CasStore {
  value: string | null = null;
  interleave: Array<() => void> = [];

  async get<T>(): Promise<T | null> {
    return this.value as T | null;
  }

  async compareAndSet<T>(_key: string, expected: T | null, next: T): Promise<boolean> {
    this.interleave.shift()?.();
    if (this.value !== expected) return false;
    this.value = next as unknown as string;
    return true;
  }

  store(document: Record<string, unknown>): void {
    this.value = `encrypted:${JSON.stringify(document)}`;
  }
}

describe('CPD-1 Labs saves are audited (CW-9)', () => {
  let kv: CasStore;
  let audit: ReturnType<typeof auditedControlPlane>;

  beforeEach(() => {
    kv = new CasStore();
    audit = auditedControlPlane();
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ algorithm: 'aes-256-gcm', secretKey: 'a'.repeat(64) } as never);
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      encrypt: (value: string) => `encrypted:${value}`,
      decrypt: (value: string) => value.replace('encrypted:', ''),
    } as never);
  });
  afterEach(() => sinon.restore());

  const save = async (body: Record<string, unknown>) => {
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis(), end: sinon.stub() };
    const next = sinon.stub();
    await inRequestOrg(setPlatformSettings(kv as never, audit.transaction))(
      { body, user: { userId: 'u-admin', orgId: ORG }, headers: {} } as never,
      res as never,
      next,
    );
    return { res, next };
  };

  it('a save that lands is one entry naming each setting it decided; no value, no `expected`', async () => {
    kv.store({ featureFlagsVersion: 2, featureFlags: {} });
    const { res, next } = await save({
      featureFlags: { ENABLE_BETA_CONNECTORS: true, ENABLE_SCHEDULED_RUNS: true },
      fileUploadMaxSizeBytes: 2048,
      expected: { featureFlags: { ENABLE_BETA_CONNECTORS: false, ENABLE_SCHEDULED_RUNS: false } },
    });
    expect(next.called).to.equal(false);
    expect(res.status.calledWith(200)).to.equal(true);
    const entries = audit.configEntries(ORG);
    expect(entries).to.have.length(1);
    expect(entries[0]!.fields).to.include({
      scope: `org:${ORG}`,
      objectType: 'platform-settings',
      objectId: 'labs',
      actor: 'user:u-admin',
      changed: 'featureFlags.ENABLE_BETA_CONNECTORS,featureFlags.ENABLE_SCHEDULED_RUNS,fileUploadMaxSizeBytes',
    });
    expect(JSON.stringify(entries)).to.not.include('2048');
    expect(JSON.stringify(entries)).to.not.include('expected');
  });

  it('a save UX2 refuses (labs_changed) records nothing and still answers 409', async () => {
    kv.store({ featureFlagsVersion: 2, featureFlags: { ENABLE_BETA_CONNECTORS: true } });
    const { next } = await save({ featureFlags: { ENABLE_BETA_CONNECTORS: false }, expected: { featureFlags: { ENABLE_BETA_CONNECTORS: false } } });
    expect(next.firstCall.args[0].statusCode).to.equal(409);
    expect(audit.configEntries(ORG)).to.deep.equal([]);
  });

  it('a save that loses every compare-and-set round records nothing', async () => {
    kv.store({ featureFlagsVersion: 2, featureFlags: {} });
    kv.interleave = Array.from({ length: 10 }, (_, i) => () => kv.store({ featureFlagsVersion: 2, featureFlags: {}, other: i }));
    const { next } = await save({ featureFlags: { ENABLE_BETA_CONNECTORS: true } });
    expect(next.firstCall.args[0].statusCode).to.equal(409);
    expect(audit.configEntries(ORG)).to.deep.equal([]);
  });

  it('two saves at once of different flags both land, each one entry at consecutive revisions', async () => {
    kv.store({ featureFlagsVersion: 2, featureFlags: {} });
    await Promise.all([save({ featureFlags: { ENABLE_BETA_CONNECTORS: true } }), save({ featureFlags: { ENABLE_PROJECTS: true } })]);
    expect(audit.configEntries(ORG).map((entry) => entry.journalSeq)).to.deep.equal([1, 2]);
    expect(JSON.parse(kv.value!.replace('encrypted:', '')).featureFlags).to.deep.equal({ ENABLE_BETA_CONNECTORS: true, ENABLE_PROJECTS: true });
  });
});
