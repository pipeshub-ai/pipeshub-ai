import 'reflect-metadata';
import { expect } from 'chai';
import { loadConfigurationManagerConfig } from '../../../../src/modules/configuration_manager/config/config';
import { StoreType } from '../../../../src/libs/keyValueStore/constants/KeyValueStoreType';

describe('loadConfigurationManagerConfig store type', () => {
  const saved = {
    KV: process.env.KV_STORE_TYPE,
    SECRET: process.env.SECRET_KEY,
  };

  beforeEach(() => {
    process.env.SECRET_KEY = 'test-secret';
  });

  afterEach(() => {
    for (const [name, value] of [
      ['KV_STORE_TYPE', saved.KV],
      ['SECRET_KEY', saved.SECRET],
    ] as const) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });

  const storeFor = (kv: string | undefined): StoreType => {
    if (kv === undefined) delete process.env.KV_STORE_TYPE;
    else process.env.KV_STORE_TYPE = kv;
    return loadConfigurationManagerConfig().storeType;
  };

  it('picks the PostgreSQL store when KV_STORE_TYPE is postgres', () => {
    expect(storeFor('postgres')).to.equal(StoreType.Postgres);
    expect(storeFor('POSTGRES')).to.equal(StoreType.Postgres);
  });

  it('keeps Redis as the default and etcd for anything else', () => {
    expect(storeFor(undefined)).to.equal(StoreType.Redis);
    expect(storeFor('redis')).to.equal(StoreType.Redis);
    expect(storeFor('etcd')).to.equal(StoreType.Etcd3);
  });
});
