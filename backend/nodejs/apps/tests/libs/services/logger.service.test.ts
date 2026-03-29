import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Logger, LogLevel, getLogLevel } from '../../../src/libs/services/logger.service';

describe('Logger', () => {
  // Reset the singleton between tests
  beforeEach(() => {
    (Logger as any).instance = null;
  });

  afterEach(() => {
    sinon.restore();
    (Logger as any).instance = null;
  });

  describe('getLogLevel', () => {
    it('should return Info as default when LOG_LEVEL is not set', () => {
      const original = process.env.LOG_LEVEL;
      delete process.env.LOG_LEVEL;
      expect(getLogLevel()).to.equal(LogLevel.Info);
      process.env.LOG_LEVEL = original;
    });

    it('should return the LOG_LEVEL when set to a valid value', () => {
      const original = process.env.LOG_LEVEL;
      process.env.LOG_LEVEL = 'debug';
      expect(getLogLevel()).to.equal(LogLevel.Debug);
      process.env.LOG_LEVEL = original;
    });

    it('should be case insensitive', () => {
      const original = process.env.LOG_LEVEL;
      process.env.LOG_LEVEL = 'ERROR';
      expect(getLogLevel()).to.equal(LogLevel.Error);
      process.env.LOG_LEVEL = original;
    });

    it('should return Info for invalid values', () => {
      const original = process.env.LOG_LEVEL;
      process.env.LOG_LEVEL = 'invalid';
      expect(getLogLevel()).to.equal(LogLevel.Info);
      process.env.LOG_LEVEL = original;
    });
  });

  describe('LogLevel enum', () => {
    it('should have correct values', () => {
      expect(LogLevel.Debug).to.equal('debug');
      expect(LogLevel.Info).to.equal('info');
      expect(LogLevel.Warning).to.equal('warning');
      expect(LogLevel.Error).to.equal('error');
    });
  });

  describe('constructor', () => {
    it('should create a new Logger instance', () => {
      const logger = new Logger({ service: 'test-service' });
      expect(logger).to.be.instanceOf(Logger);
    });

    it('should return the same instance on subsequent calls (singleton)', () => {
      const logger1 = new Logger({ service: 'service1' });
      const logger2 = new Logger({ service: 'service2' });
      expect(logger1).to.equal(logger2);
    });

    it('should use default service name when not provided', () => {
      const logger = new Logger();
      expect(logger).to.be.instanceOf(Logger);
    });
  });

  describe('getInstance', () => {
    it('should create instance if none exists', () => {
      const logger = Logger.getInstance({ service: 'test' });
      expect(logger).to.be.instanceOf(Logger);
    });

    it('should return existing instance', () => {
      const logger1 = Logger.getInstance({ service: 'test' });
      const logger2 = Logger.getInstance();
      expect(logger1).to.equal(logger2);
    });

    it('should update config on existing instance', () => {
      const logger = Logger.getInstance({ service: 'test', level: 'info' });
      Logger.getInstance({ service: 'updated', level: 'debug' });
      // The instance should be the same object
      expect(logger).to.equal(Logger.getInstance());
    });
  });

  describe('logging methods', () => {
    let logger: Logger;
    let logStub: sinon.SinonStub;

    beforeEach(() => {
      logger = new Logger({ service: 'test', level: 'debug' });
      // Stub the internal winston logger's log method
      logStub = sinon.stub((logger as any).logger, 'log');
    });

    it('should call info with correct level', () => {
      logger.info('test message');
      expect(logStub.calledOnce).to.be.true;
      const call = logStub.firstCall.args[0];
      expect(call.level).to.equal('info');
      expect(call.message).to.equal('test message');
    });

    it('should call error with correct level', () => {
      logger.error('error message');
      expect(logStub.calledOnce).to.be.true;
      const call = logStub.firstCall.args[0];
      expect(call.level).to.equal('error');
      expect(call.message).to.equal('error message');
    });

    it('should call warn with correct level', () => {
      logger.warn('warning message');
      expect(logStub.calledOnce).to.be.true;
      const call = logStub.firstCall.args[0];
      expect(call.level).to.equal('warning');
      expect(call.message).to.equal('warning message');
    });

    it('should call debug with correct level', () => {
      logger.debug('debug message');
      expect(logStub.calledOnce).to.be.true;
      const call = logStub.firstCall.args[0];
      expect(call.level).to.equal('debug');
      expect(call.message).to.equal('debug message');
    });

    it('should pass metadata to log', () => {
      logger.info('test', { key: 'value' });
      const call = logStub.firstCall.args[0];
      expect(call.metadata).to.deep.equal({ key: 'value' });
    });

    it('should include caller info (filename and line)', () => {
      logger.info('test');
      const call = logStub.firstCall.args[0];
      expect(call).to.have.property('filename');
      expect(call).to.have.property('line');
    });
  });

  describe('updateDefaultMeta', () => {
    it('should update default metadata', () => {
      const logger = new Logger({ service: 'test' });
      logger.updateDefaultMeta({ environment: 'test' });
      expect((logger as any).defaultMeta).to.have.property('environment', 'test');
    });

    it('should merge with existing metadata', () => {
      const logger = new Logger({ service: 'test' });
      logger.updateDefaultMeta({ key1: 'value1' });
      logger.updateDefaultMeta({ key2: 'value2' });
      expect((logger as any).defaultMeta).to.have.property('key1', 'value1');
      expect((logger as any).defaultMeta).to.have.property('key2', 'value2');
    });
  });

  describe('logRequest', () => {
    it('should log request details', () => {
      const logger = new Logger({ service: 'test' });
      const logStub = sinon.stub((logger as any).logger, 'log');
      const mockReq = {
        method: 'GET',
        path: '/api/v1/test',
        query: { page: '1' },
        params: { id: '123' },
        headers: { 'content-type': 'application/json', authorization: 'Bearer token', cookie: 'session=abc' },
      };
      logger.logRequest(mockReq as any);
      expect(logStub.calledOnce).to.be.true;
    });
  });

  describe('sanitizeForLogging (private)', () => {
    it('should handle circular references', () => {
      const logger = new Logger({ service: 'test' });
      const logStub = sinon.stub((logger as any).logger, 'log');

      const circular: any = { name: 'test' };
      circular.self = circular;

      // Should not throw
      logger.info('circular test', circular);
      expect(logStub.calledOnce).to.be.true;
    });

    it('should handle null and undefined values', () => {
      const logger = new Logger({ service: 'test' });
      const logStub = sinon.stub((logger as any).logger, 'log');

      logger.info('null test', { key: null, undef: undefined });
      expect(logStub.calledOnce).to.be.true;
    });

    it('should skip parent, _readableState, and pipes properties', () => {
      const logger = new Logger({ service: 'test' });
      const sanitize = (logger as any).sanitizeForLogging.bind(logger);

      const obj = {
        name: 'test',
        parent: { circular: 'data' },
        _readableState: { flowing: true },
        pipes: [1, 2, 3],
        safe: 'value',
      };

      const result = sanitize(obj);
      expect(result).to.have.property('name', 'test');
      expect(result).to.have.property('safe', 'value');
      expect(result).to.not.have.property('parent');
      expect(result).to.not.have.property('_readableState');
      expect(result).to.not.have.property('pipes');
    });

    it('should handle arrays inside objects', () => {
      const logger = new Logger({ service: 'test' });
      const sanitize = (logger as any).sanitizeForLogging.bind(logger);

      const result = sanitize({ items: [1, 'two', { nested: true }] });
      expect(result.items).to.be.an('array');
      expect(result.items).to.have.length(3);
      expect(result.items[2]).to.deep.equal({ nested: true });
    });

    it('should return [Unable to serialize] when sanitizeForLogging throws on a property', () => {
      const logger = new Logger({ service: 'test' });
      const sanitize = (logger as any).sanitizeForLogging.bind(logger);

      // Create an object whose property value causes sanitizeForLogging to throw
      // during recursive processing (inside the try-catch per key)
      const badValue = Object.create(null);
      Object.defineProperty(badValue, Symbol.toPrimitive, {
        value: () => { throw new Error('cannot convert'); },
      });
      // Make Object.entries succeed but recursive sanitize fail:
      // Use a value that is an object with a getter that throws during iteration
      const inner: any = {};
      Object.defineProperty(inner, 'x', {
        get() { throw new Error('inner getter error'); },
        enumerable: true,
      });
      const problematic: any = { good: 'ok', bad: inner };

      const result = sanitize(problematic);
      expect(result).to.have.property('good', 'ok');
      expect(result).to.have.property('bad', '[Unable to serialize]');
    });

    it('should return primitive values as-is', () => {
      const logger = new Logger({ service: 'test' });
      const sanitize = (logger as any).sanitizeForLogging.bind(logger);

      expect(sanitize(null)).to.be.null;
      expect(sanitize(42)).to.equal(42);
      expect(sanitize('hello')).to.equal('hello');
      expect(sanitize(true)).to.be.true;
      expect(sanitize(undefined)).to.be.undefined;
    });
  });
});
