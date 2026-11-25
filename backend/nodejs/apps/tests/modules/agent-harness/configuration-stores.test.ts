import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createHash } from 'crypto';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { configPaths } from '../../../src/modules/configuration_manager/paths/paths';
import {
  configurationCipher,
  createGatewayTokenStores,
  createMemoryPolicyStore,
} from '../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';
import { createModelAccountVault } from '../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import {
  HkdfKeyEncryptionKey,
  platformKeyEncryptionKey,
} from '../../../src/modules/agent-harness/adapters/crypto/hkdf-key-encryption-key';
import { ATTEMPT_TOKEN_ALGORITHM, PublishedAttemptKeys } from '../../../src/modules/agent-harness/domain/authority';
import { envGuard } from '../../helpers/env-guard';
import { MemoryKv, platformCipher } from './support/model-accounts';

import { platformCipherKey } from '../../../src/libs/encryptor/secret-key-ring';
const hashed = (secret: string): string => createHash('sha256').update(secret).digest('hex');

describe('agent-harness configuration stores under the platform secret', () => {
  const env = envGuard();

  beforeEach(() => env.snapshot());

  afterEach(() => {
    env.restore();
    sinon.restore();
  });

  it('the memory policy (P7.3) is stored encrypted at its own path and read back as written', async () => {
    const kv = new MemoryKv();
    const store = createMemoryPolicyStore(kv, platformCipher);
    const policy = { orgs: { 'org-1': { enabled: true } } };
    expect(await store.replace(null, policy)).to.equal(true);
    const stored = await kv.raw.getKey(configPaths.agentHarness.memory);
    expect(stored).to.be.a('string').and.not.include('enabled');
    expect(await store.read()).to.deep.equal({ value: policy, revision: stored });
  });

  it('the gateway keyring (E2) is kept apart from the attempt keyring, at two paths of its own', async () => {
    const kv = new MemoryKv();
    const { gatewaySigningKeys, gatewayPublicKeys } = createGatewayTokenStores(kv, platformCipher);
    const keyring = {
      activeKid: 'k1',
      keys: [{ kid: 'k1', privateKey: 'priv', publicKey: 'pub', createdAt: 1, retiredAt: null }],
      rotateId: null,
    };
    const published: PublishedAttemptKeys = {
      issuer: 'pipeshub',
      audience: 'tool-gateway',
      keys: [{ kid: 'k1', alg: ATTEMPT_TOKEN_ALGORITHM, publicKey: 'pub', retiredAt: null }],
    };
    expect(await gatewaySigningKeys.replace(null, keyring)).to.equal(true);
    expect(await gatewayPublicKeys.replace(null, published)).to.equal(true);
    expect((await gatewaySigningKeys.read()).value).to.deep.equal(keyring);
    expect((await gatewayPublicKeys.read()).value).to.deep.equal(published);
    expect(await kv.raw.getKey(configPaths.agentHarness.gatewaySigningKeys)).to.be.a('string');
    expect(await kv.raw.getKey(configPaths.agentHarness.gatewayPublicKeys)).to.be.a('string');
    expect(await kv.raw.getKey(configPaths.agentHarness.attemptSigningKeys)).to.equal(null);
  });

  it('the configuration cipher is the platform\'s, keyed by HKDF of SECRET_KEY (SEC-D-05) and its algorithm', () => {
    process.env.SECRET_KEY = '81c7198b42555dccc65e394848ca45a15b94bfc87d408bd9177768af1ef6f34d';
    process.env.ALGORITHM = 'aes-256-gcm';
    const getInstance = sinon.stub(EncryptionService, 'getInstance').returns(platformCipher as never);
    expect(configurationCipher()).to.equal(platformCipher);
    expect(getInstance.calledOnceWithExactly('aes-256-gcm', platformCipherKey('81c7198b42555dccc65e394848ca45a15b94bfc87d408bd9177768af1ef6f34d'))).to.equal(true);
  });

  it('the model-account vault looks the platform cipher up at first use, not when it is built', async () => {
    process.env.SECRET_KEY = '81c7198b42555dccc65e394848ca45a15b94bfc87d408bd9177768af1ef6f34d';
    const getInstance = sinon.stub(EncryptionService, 'getInstance').returns(platformCipher as never);
    const kv = new MemoryKv();
    const vault = createModelAccountVault(kv);
    expect(getInstance.called).to.equal(false);
    const apiKey = 'sk-proj-' + 'H5LazyCipherKey0123456789abcdWXYZ';
    await vault.put(
      'org-1',
      { kind: 'user', userId: 'u1' },
      'openAI',
      { kind: 'user_api_key', provider: 'openAI', label: null, material: { provider: 'openAI', apiKey }, hint: apiKey },
      'u1',
    );
    expect(getInstance.called).to.equal(true);
    expect(await kv.dump()).to.not.include(apiKey);
    expect((await vault.get('org-1', { kind: 'user', userId: 'u1' }, 'openAI'))?.last4).to.equal('WXYZ');
  });

  it('the self-hosted KEK is HKDF of the hashed SECRET_KEY: same secret, same key; another secret, another', async () => {
    process.env.SECRET_KEY = '13f27cd0f56ecc7f9066e9125d0d42207037a657c755e000935e1cac6974e01e';
    const platform = platformKeyEncryptionKey();
    const same = new HkdfKeyEncryptionKey(() => Buffer.from(hashed('13f27cd0f56ecc7f9066e9125d0d42207037a657c755e000935e1cac6974e01e'), 'hex'));
    expect(platform.id).to.equal(same.id);
    const context = { orgId: 'org-1', kid: 'dk-1', version: 1 };
    const dataKey = Buffer.alloc(32, 7);
    expect(await same.unwrap(await platform.wrap(dataKey, context), context)).to.deep.equal(dataKey);
    process.env.SECRET_KEY = 'c787a89f876309725a125fad93127714bbce893674983535d04cb8ecadac87d3';
    expect(platformKeyEncryptionKey().id).to.not.equal(platform.id);
  });
});
