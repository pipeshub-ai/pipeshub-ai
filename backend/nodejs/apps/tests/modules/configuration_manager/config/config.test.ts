import 'reflect-metadata';
import { expect } from 'chai';
import { readFileSync } from 'fs';
import path from 'path';
import {
  isPlaceholderSecretKey,
  loadConfigurationManagerConfig,
} from '../../../../src/modules/configuration_manager/config/config';
import { StoreType } from '../../../../src/libs/keyValueStore/constants/KeyValueStoreType';

describe('loadConfigurationManagerConfig store type', () => {
  const saved = {
    KV: process.env.KV_STORE_TYPE,
    SECRET: process.env.SECRET_KEY,
  };

  beforeEach(() => {
    process.env.SECRET_KEY = '9caf06bb4436cdbfa20af9121a626bc1093c4f54b31c0fa937957856135345b6';
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

describe('isPlaceholderSecretKey', () => {
  it('flags the shipped placeholders, with or without padding', () => {
    expect(isPlaceholderSecretKey('my-secret')).to.equal(true);
    expect(
      isPlaceholderSecretKey('your_random_encryption_secret_key'),
    ).to.equal(true);
    expect(
      isPlaceholderSecretKey('  your_random_encryption_secret_key\n'),
    ).to.equal(true);
  });

  it('flags empty values and values shorter than 16 characters', () => {
    expect(isPlaceholderSecretKey('')).to.equal(true);
    expect(isPlaceholderSecretKey('a'.repeat(15))).to.equal(true);
    expect(isPlaceholderSecretKey(' ' + 'a'.repeat(14) + ' ')).to.equal(true);
  });

  it('flags long values that are repetitive or a placeholder with a suffix', () => {
    expect(isPlaceholderSecretKey('a'.repeat(16))).to.equal(true);
    expect(isPlaceholderSecretKey('ab'.repeat(20))).to.equal(true);
    expect(isPlaceholderSecretKey('1234567'.repeat(4))).to.equal(true);
    expect(
      isPlaceholderSecretKey('your_random_encryption_secret_key_2'),
    ).to.equal(true);
    expect(isPlaceholderSecretKey('My-Secret-For-Production')).to.equal(true);
  });

  it('accepts a generated value', () => {
    expect(isPlaceholderSecretKey('k3P9vQ2mX7tL1wZ8')).to.equal(false);
    expect(isPlaceholderSecretKey('3f9c1d8e7b6a4f20c5d9e1a7b8c40d12')).to.equal(
      false,
    );
  });
});

describe('Compose SECRET_KEY defaults', () => {
  const composeDir = path.resolve(
    __dirname,
    '../../../../../../../deployment/docker-compose',
  );

  // The build and beta files refuse to start without one; the released file must keep starting an install that has none.
  it('docker-compose.yml starts without SECRET_KEY set', () => {
    const line = readFileSync(
      path.join(composeDir, 'docker-compose.yml'),
      'utf8',
    )
      .split('\n')
      .find((l) => l.includes('- SECRET_KEY='));
    expect(line).to.match(/\$\{SECRET_KEY:-/);
  });
});
