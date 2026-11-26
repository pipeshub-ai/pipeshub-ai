/**
 * UX2 LC-1..LC-5: a Labs save is a compare-and-set on the settings document.
 * Two admins saving different settings both land; a save that would decide a
 * setting someone else changed since the admin read it is refused 409
 * `labs_changed` and writes nothing.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as cmConfig from '../../../src/modules/configuration_manager/config/config';
import * as encryptorModule from '../../../src/libs/encryptor/encryptor';
import { setPlatformSettings } from '../../../src/modules/configuration_manager/controller/cm_controller';
import {
  LABS_CHANGED,
  MAX_SETTINGS_SAVE_ATTEMPTS,
  platformSettingsOf,
  savePlatformSettings,
} from '../../../src/modules/configuration_manager/utils/util';
import { platformSettingsSchema } from '../../../src/modules/configuration_manager/validator/validators';
import { ConflictError } from '../../../src/libs/errors/http.errors';
import { configPaths } from '../../../src/modules/configuration_manager/paths/paths';
import { testControlPlane } from '../control-plane/support/in-memory';
import { inRequestOrg } from '../../helpers/request-org-context';

/** The settings key as a store with an atomic compare-and-set sees it; `interleave` writes between a read and its CAS. */
class CasStore {
  value: string | null = null;
  writes = 0;
  interleave: Array<() => void> = [];

  async get<T>(key: string): Promise<T | null> {
    expect(key).to.equal(configPaths.platform.settings);
    return this.value as T | null;
  }

  async compareAndSet<T>(_key: string, expected: T | null, next: T): Promise<boolean> {
    this.interleave.shift()?.();
    if (this.value !== expected) return false;
    this.value = next as unknown as string;
    this.writes += 1;
    return true;
  }

  async set(): Promise<void> {
    throw new Error('a Labs save must not write without compare-and-set');
  }

  document(): Record<string, unknown> {
    return this.value === null ? {} : JSON.parse(this.value.replace('encrypted:', ''));
  }

  store(document: Record<string, unknown>): void {
    this.value = `encrypted:${JSON.stringify(document)}`;
  }
}

describe('Labs saves are compare-and-set (UX2)', () => {
  let kv: CasStore;
  beforeEach(() => {
    kv = new CasStore();
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ algorithm: 'aes-256-gcm', secretKey: 'a'.repeat(64) } as never);
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      encrypt: (value: string) => `encrypted:${value}`,
      decrypt: (value: string) => value.replace('encrypted:', ''),
    } as never);
  });
  afterEach(() => sinon.restore());

  const flags = () => platformSettingsOf(kv.document()).featureFlags;

  it('LC-1: two admins saving different flags at once both land', async () => {
    kv.store({ featureFlagsVersion: 2, featureFlags: {} });
    // Admin B writes ENABLE_SKILLS between admin A's read and A's write.
    kv.interleave.push(() => {
      kv.store({ featureFlagsVersion: 2, featureFlags: { ENABLE_SKILLS: false } });
    });
    await savePlatformSettings(kv as never, {
      featureFlags: { ENABLE_BETA_CONNECTORS: true },
      expected: { featureFlags: { ENABLE_BETA_CONNECTORS: false } },
    });
    expect(flags()).to.include({ ENABLE_BETA_CONNECTORS: true, ENABLE_SKILLS: false });
    expect(kv.document().featureFlags).to.deep.equal({ ENABLE_BETA_CONNECTORS: true, ENABLE_SKILLS: false });
  });

  it('LC-2: a flag someone else changed since the admin read it is refused, nothing written', async () => {
    kv.store({ featureFlagsVersion: 2, featureFlags: { ENABLE_BETA_CONNECTORS: true } });
    const before = kv.value;
    let refused: unknown;
    try {
      await savePlatformSettings(kv as never, {
        featureFlags: { ENABLE_BETA_CONNECTORS: false, ENABLE_SKILLS: false },
        expected: { featureFlags: { ENABLE_BETA_CONNECTORS: false, ENABLE_SKILLS: true } },
      });
    } catch (error) {
      refused = error;
    }
    expect((refused as { stale?: string[] }).stale).to.deep.equal(['ENABLE_BETA_CONNECTORS']);
    expect(kv.value).to.equal(before);
    expect(kv.writes).to.equal(0);
  });

  it('LC-2: the route answers 409 labs_changed with the settings to review', async () => {
    kv.store({ featureFlagsVersion: 2, featureFlags: { ENABLE_BETA_CONNECTORS: true } });
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis(), end: sinon.stub() };
    const next = sinon.stub();
    await inRequestOrg(setPlatformSettings(kv as never, testControlPlane()))(
      { body: { featureFlags: { ENABLE_BETA_CONNECTORS: true, ENABLE_SKILLS: false }, expected: { featureFlags: { ENABLE_BETA_CONNECTORS: false, ENABLE_SKILLS: true } } } } as never,
      res as never,
      next,
    );
    // ENABLE_BETA_CONNECTORS is already what the admin sends; ENABLE_SKILLS still holds what they saw: nothing to refuse.
    expect(next.called).to.equal(false);
    kv.store({ featureFlagsVersion: 2, featureFlags: { ENABLE_SKILLS: true, ENABLE_BETA_CONNECTORS: true } });
    await inRequestOrg(setPlatformSettings(kv as never, testControlPlane()))(
      { body: { featureFlags: { ENABLE_BETA_CONNECTORS: false }, expected: { featureFlags: { ENABLE_BETA_CONNECTORS: false } } } } as never,
      res as never,
      next,
    );
    const error = next.firstCall?.args[0];
    expect(error).to.be.instanceOf(ConflictError);
    expect(error.statusCode).to.equal(409);
    expect(error.metadata.reasonCode).to.equal(LABS_CHANGED);
    expect(error.metadata.stale).to.deep.equal(['ENABLE_BETA_CONNECTORS']);
  });

  it('LC-3: the stored value already being what the admin sends is no conflict', async () => {
    kv.store({ featureFlagsVersion: 2, featureFlags: { ENABLE_BETA_CONNECTORS: true } });
    await savePlatformSettings(kv as never, {
      featureFlags: { ENABLE_BETA_CONNECTORS: true },
      expected: { featureFlags: { ENABLE_BETA_CONNECTORS: false } },
    });
    expect(flags()['ENABLE_BETA_CONNECTORS']).to.equal(true);
  });

  it('LC-2: the upload limit follows the same rule', async () => {
    kv.store({ fileUploadMaxSizeBytes: 3000 });
    let refused: unknown;
    try {
      await savePlatformSettings(kv as never, { fileUploadMaxSizeBytes: 1000, expected: { fileUploadMaxSizeBytes: 2000 } });
    } catch (error) {
      refused = error;
    }
    expect((refused as { stale?: string[] }).stale).to.deep.equal(['fileUploadMaxSizeBytes']);
    await savePlatformSettings(kv as never, { fileUploadMaxSizeBytes: 1000, expected: { fileUploadMaxSizeBytes: 3000 } });
    expect(kv.document().fileUploadMaxSizeBytes).to.equal(1000);
  });

  it('LC-4: a lost race is retried; losing every round is 409', async () => {
    kv.store({ featureFlagsVersion: 2, featureFlags: {} });
    kv.interleave.push(() => kv.store({ featureFlagsVersion: 2, featureFlags: { ENABLE_SOFT_DELETE: true } }));
    await savePlatformSettings(kv as never, { featureFlags: { ENABLE_BETA_CONNECTORS: true } });
    expect(kv.document().featureFlags).to.deep.equal({ ENABLE_SOFT_DELETE: true, ENABLE_BETA_CONNECTORS: true });

    let n = 0;
    for (let i = 0; i < MAX_SETTINGS_SAVE_ATTEMPTS; i += 1) {
      kv.interleave.push(() => kv.store({ featureFlagsVersion: 2, featureFlags: {}, round: (n += 1) }));
    }
    let refused: unknown;
    try {
      await savePlatformSettings(kv as never, { featureFlags: { ENABLE_BETA_CONNECTORS: true } });
    } catch (error) {
      refused = error;
    }
    expect((refused as Error).message).to.equal(LABS_CHANGED);
    expect(n).to.equal(MAX_SETTINGS_SAVE_ATTEMPTS);
  });

  it('LC-5: a save without expected values (an older page, the notice) merges as before', async () => {
    kv.store({ featureFlagsVersion: 2, featureFlags: { ENABLE_BETA_CONNECTORS: true }, softDeletePurge: { enabled: false } });
    await savePlatformSettings(kv as never, { featureFlags: { ENABLE_BETA_CONNECTORS: false } });
    expect(flags()['ENABLE_BETA_CONNECTORS']).to.equal(false);
    expect(kv.document().softDeletePurge).to.deep.equal({ enabled: false });
  });

  it('LC-1: the first save of a deployment creates the document only if nobody did', async () => {
    kv.interleave.push(() => kv.store({ featureFlagsVersion: 2, featureFlags: { ENABLE_SKILLS: false } }));
    await savePlatformSettings(kv as never, { featureFlags: { ENABLE_BETA_CONNECTORS: true } });
    expect(kv.document().featureFlags).to.deep.equal({ ENABLE_SKILLS: false, ENABLE_BETA_CONNECTORS: true });
  });

  it('validates expected strictly', () => {
    const ok = platformSettingsSchema.safeParse({ body: { featureFlags: { A: true }, expected: { featureFlags: { A: false } } } });
    expect(ok.success).to.equal(true);
    const bad = platformSettingsSchema.safeParse({ body: { expected: { featureFlags: { A: 'yes' } } } });
    expect(bad.success).to.equal(false);
    const extra = platformSettingsSchema.safeParse({ body: { expected: { other: 1 } } });
    expect(extra.success).to.equal(false);
  });
});
