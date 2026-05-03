import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';

// Use dynamic require to ensure c8 tracks coverage in parallel mode.
// Static imports can cause coverage to be "claimed" by a different worker
// (e.g. redis.service.test.ts imports redis.errors transitively) without
// executing all constructors, leaving some lines uncovered.

describe('Redis Errors', () => {
  let RedisError: any;
  let RedisServiceNotInitializedError: any;
  let RedisConnectionError: any;
  let RedisCacheError: any;

  before(() => {
    const modulePath = require.resolve('../../../src/libs/errors/redis.errors');
    delete require.cache[modulePath];
    const mod = require('../../../src/libs/errors/redis.errors');
    RedisError = mod.RedisError;
    RedisServiceNotInitializedError = mod.RedisServiceNotInitializedError;
    RedisConnectionError = mod.RedisConnectionError;
    RedisCacheError = mod.RedisCacheError;
  });

  describe('RedisError', () => {
    it('should have correct name', () => {
      const error = new RedisError('Redis failed');
      expect(error.name).to.equal('RedisError');
    });

    it('should have correct code', () => {
      const error = new RedisError('Redis failed');
      expect(error.code).to.equal('REDIS_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new RedisError('Redis failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new RedisError('Redis failed');
      expect(error.message).to.equal('Redis failed');
    });

    it('should be instanceof BaseError', () => {
      const error = new RedisError('Redis failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new RedisError('Redis failed');
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { host: 'localhost', port: 6379 };
      const error = new RedisError('Redis failed', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should leave metadata undefined when not provided', () => {
      const error = new RedisError('Redis failed');
      expect(error.metadata).to.be.undefined;
    });

    it('should have a stack trace', () => {
      const error = new RedisError('Redis failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new RedisError('Redis failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'RedisError');
      expect(json).to.have.property('code', 'REDIS_ERROR');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'Redis failed');
    });
  });

  describe('RedisServiceNotInitializedError', () => {
    it('should have correct name', () => {
      const error = new RedisServiceNotInitializedError('service down');
      expect(error.name).to.equal('RedisServiceNotInitializedError');
    });

    it('should have correct code', () => {
      const error = new RedisServiceNotInitializedError('service down');
      expect(error.code).to.equal('REDIS_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new RedisServiceNotInitializedError('service down');
      expect(error.statusCode).to.equal(500);
    });

    it('should prepend "Redis service not initialized: " to message', () => {
      const error = new RedisServiceNotInitializedError('service down');
      expect(error.message).to.equal('Redis service not initialized: service down');
    });

    it('should be instanceof RedisError', () => {
      const error = new RedisServiceNotInitializedError('service down');
      expect(error).to.be.an.instanceOf(RedisError);
    });

    it('should be instanceof BaseError', () => {
      const error = new RedisServiceNotInitializedError('service down');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { serviceName: 'cache' };
      const error = new RedisServiceNotInitializedError('service down', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new RedisServiceNotInitializedError('service down');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new RedisServiceNotInitializedError('service down');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'RedisServiceNotInitializedError');
      expect(json).to.have.property('code', 'REDIS_ERROR');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'Redis service not initialized: service down');
    });
  });

  describe('RedisConnectionError', () => {
    it('should have correct name', () => {
      const error = new RedisConnectionError('connection refused');
      expect(error.name).to.equal('RedisConnectionError');
    });

    it('should have correct code', () => {
      const error = new RedisConnectionError('connection refused');
      expect(error.code).to.equal('REDIS_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new RedisConnectionError('connection refused');
      expect(error.statusCode).to.equal(500);
    });

    it('should prepend "Redis connection error: " to message', () => {
      const error = new RedisConnectionError('connection refused');
      expect(error.message).to.equal('Redis connection error: connection refused');
    });

    it('should be instanceof RedisError', () => {
      const error = new RedisConnectionError('connection refused');
      expect(error).to.be.an.instanceOf(RedisError);
    });

    it('should be instanceof BaseError', () => {
      const error = new RedisConnectionError('connection refused');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { host: 'redis.example.com', port: 6379 };
      const error = new RedisConnectionError('connection refused', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new RedisConnectionError('connection refused');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new RedisConnectionError('connection refused');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'RedisConnectionError');
      expect(json).to.have.property('code', 'REDIS_ERROR');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'Redis connection error: connection refused');
    });
  });

  describe('RedisCacheError', () => {
    it('should have correct name', () => {
      const error = new RedisCacheError('cache miss');
      expect(error.name).to.equal('RedisCacheError');
    });

    it('should have correct code', () => {
      const error = new RedisCacheError('cache miss');
      expect(error.code).to.equal('REDIS_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new RedisCacheError('cache miss');
      expect(error.statusCode).to.equal(500);
    });

    it('should prepend "Redis cache error: " to message', () => {
      const error = new RedisCacheError('cache miss');
      expect(error.message).to.equal('Redis cache error: cache miss');
    });

    it('should be instanceof RedisError', () => {
      const error = new RedisCacheError('cache miss');
      expect(error).to.be.an.instanceOf(RedisError);
    });

    it('should be instanceof BaseError', () => {
      const error = new RedisCacheError('cache miss');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { key: 'user:123', operation: 'GET' };
      const error = new RedisCacheError('cache miss', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new RedisCacheError('cache miss');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new RedisCacheError('cache miss');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'RedisCacheError');
      expect(json).to.have.property('code', 'REDIS_ERROR');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'Redis cache error: cache miss');
    });
  });
});
