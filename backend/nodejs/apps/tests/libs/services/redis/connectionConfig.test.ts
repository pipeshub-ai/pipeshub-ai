import { expect } from 'chai';

import {
  DEFAULT_CLIENT_OPTIONS,
  redisCacheConnectionConfig,
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
  const keys = ['REDIS_TLS_ENABLED', 'REDIS_TLS_REJECT_UNAUTHORIZED', 'REDIS_TLS'];

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

  // A typo has no safe reading: REDIS_TLS_ENABLED=ture would silently mean
  // plaintext and REDIS_TLS_REJECT_UNAUTHORIZED=yse would silently mean
  // unverified. Refusing to start beats guessing either one.
  it('refuses an unparseable REDIS_TLS_ENABLED instead of guessing', () => {
    process.env.REDIS_TLS_ENABLED = 'ture';
    expect(() => redisConnectionConfigFromEnv()).to.throw(
      /REDIS_TLS_ENABLED='ture' is not a valid boolean/,
    );
  });

  it('refuses an unparseable REDIS_TLS_REJECT_UNAUTHORIZED', () => {
    process.env.REDIS_TLS_REJECT_UNAUTHORIZED = 'yse';
    expect(() => redisConnectionConfigFromEnv()).to.throw(
      /REDIS_TLS_REJECT_UNAUTHORIZED='yse' is not a valid boolean/,
    );
  });

  it('names the legacy REDIS_TLS alias when that is what was set', () => {
    process.env.REDIS_TLS = 'ture';
    expect(() => redisConnectionConfigFromEnv()).to.throw(/REDIS_TLS='ture'/);
    delete process.env.REDIS_TLS;
  });

  it('treats a blank value as unset, not a typo', () => {
    // Compose writes `VAR=${VAR:-}`, so blank is the normal "not configured".
    process.env.REDIS_TLS_ENABLED = '   ';
    process.env.REDIS_TLS_REJECT_UNAUTHORIZED = '';
    const config = redisConnectionConfigFromEnv();
    expect(config.tls).to.equal(false);
    expect(config.tlsRejectUnauthorized).to.equal(true);
  });
});

describe('redisCacheConnectionConfig (REDIS_CACHE_URL)', () => {
  const savedEnv = { ...process.env };
  const MAIN = { host: 'main-redis', port: 6379, password: 'main-secret', db: 0 };

  beforeEach(() => {
    for (const name of [
      'REDIS_CACHE_URL',
      'REDIS_PASSWORD',
      'REDIS_USERNAME',
      'REDIS_TLS_ENABLED',
      'REDIS_TLS',
      'REDIS_CLUSTER_ENDPOINTS',
    ]) {
      delete process.env[name];
    }
    process.env.REDIS_KEY_NAMESPACE = 'ns';
  });

  afterEach(() => {
    process.env = { ...savedEnv };
  });

  it('is the main Redis, unchanged, when unset or blank', () => {
    expect(redisCacheConnectionConfig(MAIN)).to.deep.equal(
      redisConnectionConfigFromHostPort(MAIN),
    );
    process.env.REDIS_CACHE_URL = '  ';
    expect(redisCacheConnectionConfig(MAIN).host).to.equal('main-redis');
  });

  it('takes the whole connection from the URL', () => {
    process.env.REDIS_CACHE_URL =
      'rediss://cacheuser:p%40ss%3Aw@cache-redis:6390/3';
    process.env.REDIS_PASSWORD = 'main-secret';
    process.env.REDIS_CLUSTER_ENDPOINTS = 'main-a:6379,main-b:6379';
    const cfg = redisCacheConnectionConfig(MAIN);
    expect([cfg.host, cfg.port, cfg.username, cfg.password, cfg.db, cfg.tls]).to.deep.equal(
      ['cache-redis', 6390, 'cacheuser', 'p@ss:w', 3, true],
    );
    // On cluster the URL's host is the seed, not the main cluster's endpoints.
    expect(cfg.clusterEndpoints).to.deep.equal([]);
    expect(cfg.keyNamespace).to.equal('ns');
  });

  it('never borrows the main credentials or TLS flag', () => {
    process.env.REDIS_CACHE_URL = 'redis://cache-redis';
    process.env.REDIS_PASSWORD = 'main-secret';
    process.env.REDIS_USERNAME = 'main-user';
    process.env.REDIS_TLS_ENABLED = 'true';
    const cfg = redisCacheConnectionConfig(MAIN);
    expect([cfg.port, cfg.username, cfg.password, cfg.db, cfg.tls]).to.deep.equal(
      [6379, undefined, undefined, 0, false],
    );
  });

  it('accepts an IPv6 host', () => {
    process.env.REDIS_CACHE_URL = 'redis://[::1]:6391';
    const cfg = redisCacheConnectionConfig(MAIN);
    expect([cfg.host, cfg.port]).to.deep.equal(['::1', 6391]);
  });

  for (const url of [
    'cache-redis:6379',
    'http://cache-redis',
    'redis://',
    'redis://cache-redis:notaport',
    'redis://cache-redis/x',
    'redis://cache-redis:70000',
  ]) {
    it(`refuses the malformed URL '${url}'`, () => {
      process.env.REDIS_CACHE_URL = url;
      expect(() => redisCacheConnectionConfig(MAIN)).to.throw(/REDIS_CACHE_URL/);
    });
  }
});
