/**
 * S1R SR-15: Labs no longer offers "Enable Code Tasks" (stage 1 retired). Since CTL-5 a stored value of a retired
 * flag is ignored and dropped by the next save; the settings read never adds it back.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service';
import { PLATFORM_FEATURE_FLAGS } from '../../../src/modules/configuration_manager/constants/constants';
import { getPlatformSettingsFromStore } from '../../../src/modules/configuration_manager/utils/util';

const storeHolding = (stored: unknown): KeyValueStoreService =>
  ({ get: async () => (stored === null ? null : JSON.stringify(stored)) }) as unknown as KeyValueStoreService;

describe('S1R: the retired Labs flag ENABLE_CODE_TASK', () => {
  const secret = process.env.SECRET_KEY;
  beforeEach(() => {
    process.env.SECRET_KEY = secret ?? '2ceac6f36363c6246a64cca805cd43ca7a01b14eb2fcc532ceec3f60f2f7df1c';
    sinon.stub(EncryptionService, 'getInstance').returns({
      decrypt: (text: string) => text,
    } as unknown as EncryptionService);
  });
  afterEach(() => {
    sinon.restore();
    if (secret === undefined) delete process.env.SECRET_KEY;
    else process.env.SECRET_KEY = secret;
  });

  it('is not in the Labs registry', () => {
    expect(PLATFORM_FEATURE_FLAGS.map((flag) => flag.key)).to.not.include('ENABLE_CODE_TASK');
  });

  it('a stored true is ignored, never shown (CTL-5); a store without it never gains it', async () => {
    const saved = await getPlatformSettingsFromStore(storeHolding({ featureFlags: { ENABLE_CODE_TASK: true } }));
    expect(saved.featureFlags).to.not.have.property('ENABLE_CODE_TASK');
    const fresh = await getPlatformSettingsFromStore(storeHolding(null));
    expect(fresh.featureFlags).to.not.have.property('ENABLE_CODE_TASK');
  });
});
