import { expect } from 'chai';

import {
  DEFAULT_CLIENT_OPTIONS,
  redisConnectionConfigFromEnv,
  redisConnectionConfigFromHostPort,
} from '../../../../src/libs/services/redis/connectionConfig';

describe('redisConnectionConfigFromEnv', () => {
  const savedEnv = { ...process.env };

  afterEach(() => {
    process.env = { ...savedEnv };
  });

  it('applies defaults when nothing is set', () => {
    delete process.env.REDIS_HOST;
    delete process.env.REDIS_PORT;
    delete process.env.REDIS_DB;
    delete process.env.REDIS_TLS_ENABLED;
    delete process.env.REDIS_CLUSTER_ENDPOINTS;

    const config = redisConnectionConfigFromEnv();

    expect(config.host).to.equal('localhost');
    expect(config.port).to.equal(6379);
    expect(config.db).to.equal(0);
    expect(config.tls).to.equal(false);
    expect(config.clusterEndpoints).to.deep.equal([]);
    expect(config.scaleReads).to.equal('master');
  });

  it('parses cluster endpoints as a comma-separated list', () => {
    process.env.REDIS_CLUSTER_ENDPOINTS = 'n1:7000, n2:7001,n3:7002';
    const config = redisConnectionConfigFromEnv();
    expect(config.clusterEndpoints).to.deep.equal([
      'n1:7000',
      'n2:7001',
      'n3:7002',
    ]);
  });

  it('reads TLS and namespace overrides', () => {
    process.env.REDIS_TLS_ENABLED = 'true';
    process.env.REDIS_KEY_NAMESPACE = 'tenant-a';
    const config = redisConnectionConfigFromEnv();
    expect(config.tls).to.equal(true);
    expect(config.keyNamespace).to.equal('tenant-a');
  });

  it('treats an empty REDIS_PASSWORD as unset', () => {
    process.env.REDIS_PASSWORD = '';
    const config = redisConnectionConfigFromEnv();
    expect(config.password).to.equal(undefined);
  });
});

describe('redisConnectionConfigFromHostPort', () => {
  it('overrides host/port/password/db on top of the env baseline', () => {
    const config = redisConnectionConfigFromHostPort({
      host: 'custom-host',
      port: 1234,
      password: 'secret',
      db: 3,
    });
    expect(config.host).to.equal('custom-host');
    expect(config.port).to.equal(1234);
    expect(config.password).to.equal('secret');
    expect(config.db).to.equal(3);
  });

  it('defaults db to 0 when not provided', () => {
    const config = redisConnectionConfigFromHostPort({
      host: 'h',
      port: 1,
    });
    expect(config.db).to.equal(0);
  });
});

describe('DEFAULT_CLIENT_OPTIONS', () => {
  it('is non-blocking with sane retry defaults', () => {
    expect(DEFAULT_CLIENT_OPTIONS.blocking).to.equal(false);
    expect(DEFAULT_CLIENT_OPTIONS.maxRetriesPerRequest).to.equal(3);
    expect(DEFAULT_CLIENT_OPTIONS.enableOfflineQueue).to.equal(true);
  });
});

describe('REDIS_TLS_* boolean spellings', () => {
  // Matching only the literal 'true' meant REDIS_TLS_ENABLED=1 produced a
  // plaintext connection still carrying the Redis password, and -- because it
  // defaults on -- REDIS_TLS_REJECT_UNAUTHORIZED=yes silently disabled
  // certificate verification. Both fail open with nothing logged.
  const saved: Record<string, string | undefined> = {};
  const keys = ['REDIS_TLS_ENABLED', 'REDIS_TLS_REJECT_UNAUTHORIZED'];

  beforeEach(() => {
    keys.forEach((k) => {
      saved[k] = process.env[k];
      delete process.env[k];
    });
  });

  afterEach(() => {
    keys.forEach((k) => {
      if (saved[k] === undefined) {
        delete process.env[k];
      } else {
        process.env[k] = saved[k];
      }
    });
  });

  ['1', 'yes', 'on', 'true', 'TRUE', ' true '].forEach((value) => {
    it(`treats '${value}' as TLS enabled`, () => {
      process.env.REDIS_TLS_ENABLED = value;
      expect(redisConnectionConfigFromEnv().tls).to.equal(true);
    });
  });

  ['0', 'no', 'off', 'false', ' FALSE '].forEach((value) => {
    it(`treats '${value}' as verification disabled`, () => {
      process.env.REDIS_TLS_REJECT_UNAUTHORIZED = value;
      expect(redisConnectionConfigFromEnv().tlsRejectUnauthorized).to.equal(false);
    });
  });

  it('never weakens the default on an unparseable value', () => {
    process.env.REDIS_TLS_ENABLED = 'garbage';
    process.env.REDIS_TLS_REJECT_UNAUTHORIZED = 'garbage';
    const config = redisConnectionConfigFromEnv();
    expect(config.tls).to.equal(false);
    expect(config.tlsRejectUnauthorized).to.equal(true);
  });
});
