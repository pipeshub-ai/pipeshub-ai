import { expect } from 'chai';
import {
  POSTGRES_SETTINGS,
  applyStoreUmbrella,
} from '../../../src/libs/config/storeUmbrella';

describe('PIPESHUB_STORE', () => {
  it('changes nothing when unset', () => {
    const env: NodeJS.ProcessEnv = {};
    expect(applyStoreUmbrella(env)).to.equal(false);
    expect(env).to.deep.equal({});
  });

  it('fills in every unset store setting', () => {
    const env: NodeJS.ProcessEnv = { PIPESHUB_STORE: 'Postgres' };
    expect(applyStoreUmbrella(env)).to.equal(true);
    for (const [key, value] of Object.entries(POSTGRES_SETTINGS)) {
      expect(env[key]).to.equal(value);
    }
  });

  it('keeps a setting that is already set', () => {
    const env: NodeJS.ProcessEnv = {
      PIPESHUB_STORE: 'postgres',
      MESSAGE_BROKER: 'kafka',
      REDIS_MODE: 'standalone',
    };
    applyStoreUmbrella(env);
    expect(env.MESSAGE_BROKER).to.equal('kafka');
    expect(env.REDIS_MODE).to.equal('standalone');
    expect(env.KV_STORE_TYPE).to.equal('postgres');
  });

  it('refuses a value it does not know', () => {
    expect(() => applyStoreUmbrella({ PIPESHUB_STORE: 'sqlite' })).to.throw('not supported');
  });
});
