/**
 * UX1 LB-12: Labs overrides on the real key-value store (Redis, REDIS_IT_URL)
 * with the real encryption: a document saved before this release is read
 * without a write, a save records only changes and keeps what it does not
 * send, and the notice's answer sticks. Keys live under a prefix of this
 * test's own and are removed afterwards.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { loadConfigurationManagerConfig } from '../../../src/modules/configuration_manager/config/config';
import { configPaths } from '../../../src/modules/configuration_manager/paths/paths';
import { setPlatformSettings } from '../../../src/modules/configuration_manager/controller/cm_controller';
import {
  getPlatformSettingsFromStore,
  PlatformSettingsChanged,
  savePlatformSettings,
} from '../../../src/modules/configuration_manager/utils/util';
import { testControlPlane } from '../control-plane/support/in-memory';
import { inRequestOrg } from '../../helpers/request-org-context';

describe('Labs overrides on the Redis key-value store (UX1 itest)', function () {
  this.timeout(60_000);
  let kvs: KeyValueStoreService;
  const saved: Record<string, string | undefined> = {};

  before(async () => {
    const raw = process.env.REDIS_IT_URL;
    if (!raw) throw new Error('REDIS_IT_URL must be set for the Labs overrides Redis test');
    const url = new URL(raw);
    for (const name of ['KV_STORE_TYPE', 'REDIS_HOST', 'REDIS_PORT', 'REDIS_KV_PREFIX', 'SECRET_KEY']) {
      saved[name] = process.env[name];
    }
    process.env.KV_STORE_TYPE = 'redis';
    process.env.REDIS_HOST = url.hostname;
    process.env.REDIS_PORT = url.port || '6379';
    process.env.REDIS_KV_PREFIX = `pxh_ux1:${randomUUID()}:kv:`;
    process.env.SECRET_KEY = process.env.SECRET_KEY ?? '71d7ad239615f68991abf69269cad1d7e541d252f090b43938751947dcaf00cd';
    kvs = KeyValueStoreService.getInstance(loadConfigurationManagerConfig());
    await kvs.connect();
  });

  after(async () => {
    await kvs.delete(configPaths.platform.settings);
    await kvs.disconnect();
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });

  const config = () => loadConfigurationManagerConfig();
  const writeRaw = async (document: Record<string, unknown>) =>
    kvs.set(
      configPaths.platform.settings,
      EncryptionService.getInstance(config().algorithm, config().secretKey).encrypt(JSON.stringify(document)),
    );
  const readRaw = async () =>
    JSON.parse(
      EncryptionService.getInstance(config().algorithm, config().secretKey).decrypt(
        (await kvs.get<string>(configPaths.platform.settings)) as string,
      ),
    );
  const save = async (body: Record<string, unknown>) => {
    let failure: unknown;
    await inRequestOrg(setPlatformSettings(kvs, testControlPlane()))(
      { body } as never,
      { status: () => ({ json: () => ({ end: () => undefined }) }) } as never,
      (error?: unknown) => {
        failure = error;
      },
    );
    expect(failure).to.equal(undefined);
  };

  // BETA: the release default is off; a stored "on" from before overrides is the previous default, kept and pending.
  it('reads a before-release document without writing it, and keeps a stored durable "on"', async () => {
    const before = { fileUploadMaxSizeBytes: 4096, featureFlags: { ENABLE_AGENT_HARNESS: true, ENABLE_ACTIONS: true } };
    await writeRaw(before);
    const ciphertext = await kvs.get<string>(configPaths.platform.settings);
    const settings = await getPlatformSettingsFromStore(kvs);
    expect(settings.featureFlags['ENABLE_AGENT_HARNESS']).to.equal(true);
    expect(settings.featureFlags['ENABLE_ACTIONS']).to.equal(true);
    expect(settings.featureFlagOverrides).to.deep.equal({ ENABLE_AGENT_HARNESS: true });
    expect(settings.notices).to.deep.equal({ durableAgentDefault: 'pending' });
    expect(await kvs.get<string>(configPaths.platform.settings)).to.equal(ciphertext);
  });

  it('a Labs save records only the change and carries the pending notice', async () => {
    await save({ featureFlags: { ENABLE_BETA_CONNECTORS: true } });
    const stored = await readRaw();
    expect(stored.featureFlagsVersion).to.equal(2);
    expect(stored.featureFlags).to.deep.equal({ ENABLE_AGENT_HARNESS: true, ENABLE_BETA_CONNECTORS: true });
    expect(stored.fileUploadMaxSizeBytes).to.equal(4096);
    expect(stored.notices).to.deep.equal({ durableAgentDefault: 'pending' });
  });

  it('"adopted" keeps the harness on, stored since the default is off, and the notice never shows again', async () => {
    await save({ notices: { durableAgentDefault: 'adopted' } });
    const settings = await getPlatformSettingsFromStore(kvs);
    expect(settings.featureFlags['ENABLE_AGENT_HARNESS']).to.equal(true);
    expect(settings.featureFlagOverrides).to.deep.equal({ ENABLE_AGENT_HARNESS: true, ENABLE_BETA_CONNECTORS: true });
    expect(settings.notices).to.deep.equal({ durableAgentDefault: 'adopted' });
  });

  it('"Keep it off" on another install records the choice', async () => {
    await writeRaw({ featureFlags: { ENABLE_AGENT_HARNESS: false } });
    await save({ notices: { durableAgentDefault: 'kept' } });
    const settings = await getPlatformSettingsFromStore(kvs);
    expect(settings.featureFlags['ENABLE_AGENT_HARNESS']).to.equal(false);
    expect(settings.notices).to.deep.equal({ durableAgentDefault: 'kept' });
  });

  it('UX2 LC-7: concurrent saves of different flags both land; a stale expected value is 409', async () => {
    await writeRaw({ featureFlagsVersion: 2, featureFlags: {} });
    const flags = ['ENABLE_BETA_CONNECTORS', 'ENABLE_SOFT_DELETE', 'ENABLE_VECTOR_STORE_REBUILD', 'ENABLE_KNOWLEDGE_PUBLICATIONS'];
    await Promise.all(
      flags.map((flag) => savePlatformSettings(kvs, { featureFlags: { [flag]: true }, expected: { featureFlags: { [flag]: false } } })),
    );
    const settings = await getPlatformSettingsFromStore(kvs);
    for (const flag of flags) expect(settings.featureFlags[flag], flag).to.equal(true);

    let refused: unknown;
    try {
      await savePlatformSettings(kvs, { featureFlags: { ENABLE_BETA_CONNECTORS: false }, expected: { featureFlags: { ENABLE_BETA_CONNECTORS: false } } });
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(PlatformSettingsChanged);
    expect((await getPlatformSettingsFromStore(kvs)).featureFlags['ENABLE_BETA_CONNECTORS']).to.equal(true);
  });
});
