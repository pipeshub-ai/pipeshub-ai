import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import fs from 'fs';
import os from 'os';
import path from 'path';
import winston from 'winston';
import {
  Logger,
  LogLevel,
  fileLogDirectory,
  getLogLevel,
} from '../../../src/libs/services/logger.service';
import { FileSink } from '../../../src/libs/services/log-file-sink';
import { InvalidEnvFlagError } from '../../../src/libs/utils/env-flags';
import { Types } from 'mongoose';
import { envGuard } from '../../helpers/env-guard';

describe('Logger', () => {
  const env = envGuard();
  // Modules loaded earlier in this mocha worker hold the process's logger; leaving the singleton null makes later
  // files' `Logger.getInstance()` a different logger from the one their code under test writes to.
  let processLogger: Logger | undefined;

  before(() => {
    processLogger = (Logger as any).instance;
  });

  after(() => {
    (Logger as any).instance = processLogger;
  });

  beforeEach(() => {
    env.snapshot();
    (Logger as any).instance = null;
  });

  afterEach(() => {
    env.restore();
    sinon.restore();
    (Logger as any).instance = null;
  });

  describe('getLogLevel', () => {
    it('should return Info as default when LOG_LEVEL is not set', () => {
      delete process.env.LOG_LEVEL;
      expect(getLogLevel()).to.equal(LogLevel.Info);
    });

    it('should return the LOG_LEVEL when set to a valid value', () => {
      process.env.LOG_LEVEL = 'debug';
      expect(getLogLevel()).to.equal(LogLevel.Debug);
    });

    it('should be case insensitive', () => {
      process.env.LOG_LEVEL = 'ERROR';
      expect(getLogLevel()).to.equal(LogLevel.Error);
    });

    it('should return Info for invalid values', () => {
      process.env.LOG_LEVEL = 'invalid';
      expect(getLogLevel()).to.equal(LogLevel.Info);
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

    it('returns one logger per service name', () => {
      const logger1 = new Logger({ service: 'service1' });
      expect(new Logger({ service: 'service1' })).to.equal(logger1);
      expect(new Logger({ service: 'service2' })).to.not.equal(logger1);
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
      expect(Logger.getInstance({ service: 'test' })).to.equal(logger1);
      expect(Logger.getInstance()).to.equal(Logger.getInstance());
    });

    it('takes its level from LOG_LEVEL when created without a config', () => {
      process.env.LOG_LEVEL = 'debug';
      const logger = Logger.getInstance();
      expect((logger as any).logger.level).to.equal('debug');
    });

    it('defaults to info when created without a config or LOG_LEVEL', () => {
      delete process.env.LOG_LEVEL;
      const logger = Logger.getInstance();
      expect((logger as any).logger.level).to.equal('info');
    });

    it('applies a configured level to every logger', () => {
      const logger = Logger.getInstance({ service: 'test', level: 'info' });
      Logger.getInstance({ service: 'updated', level: 'debug' });
      expect((logger as any).logger.level).to.equal('debug');
      expect((Logger.getInstance() as any).logger).to.equal((logger as any).logger);
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
      const logged = JSON.stringify(logStub.firstCall.args);
      expect(logged).to.not.contain('Bearer token');
      expect(logged).to.not.contain('session=abc');
    });

    it('should redact OAuth codes from the query and the referer', () => {
      const logger = new Logger({ service: 'test' });
      const logStub = sinon.stub((logger as any).logger, 'log');
      logger.logRequest({
        method: 'GET',
        path: '/api/v1/connectors/oauth/callback',
        query: { code: 'fake-code-value', page: '1' },
        params: {},
        headers: { referer: 'http://localhost:3001/cb?code=fake-code-value', 'x-session-token': 'fake-session' },
      } as any);
      const logged = JSON.stringify(logStub.firstCall.args);
      expect(logged).to.not.contain('fake-code-value');
      expect(logged).to.not.contain('fake-session');
    });
  });

  describe('metadata redaction format', () => {
    it('redacts URL-valued fields before any transport formats the line', () => {
      const logger = new Logger({ service: 'test' });
      const format = (logger as any).logger.format;
      const info = format.transform(
        {
          level: 'error',
          message: 'Application error',
          metadata: {
            context: {
              request: {
                path: '/api/v1/connectors/oauth/callback',
                headers: {
                  referer: 'http://localhost:3001/connectors/oauth/callback/Drive?state=fake-state-value&code=fake-code-value',
                  authorization: 'Bearer fake-bearer-value',
                },
              },
            },
            redirectUrl: '/oauth/authorize?state=fake-state-value',
          },
        },
        {},
      );
      const rendered = String(info[Symbol.for('message')]) + JSON.stringify(info.metadata);
      for (const secret of ['fake-code-value', 'fake-state-value', 'fake-bearer-value']) {
        expect(rendered).to.not.contain(secret);
      }
      expect(rendered).to.contain('/connectors/oauth/callback/Drive');
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

    it('should redact credential-bearing keys regardless of separator or case', () => {
      const logger = new Logger({ service: 'test' });
      const sanitize = (logger as any).sanitizeForLogging.bind(logger);

      const result = sanitize({
        Authorization: 'Bearer abc.def.ghi',
        'api-key': 'sk-live-1',
        api_key: 'sk-live-2',
        apiKey: 'sk-live-3',
        cookie: 'session=1',
        refreshToken: 'rt-1',
        clientSecret: 'cs-1',
        'x-slack-signature': 'v0=deadbeef',
        url: 'https://example.test/v1/chat',
      });

      expect(result.Authorization).to.equal('[REDACTED]');
      expect(result['api-key']).to.equal('[REDACTED]');
      expect(result.api_key).to.equal('[REDACTED]');
      expect(result.apiKey).to.equal('[REDACTED]');
      expect(result.cookie).to.equal('[REDACTED]');
      expect(result.refreshToken).to.equal('[REDACTED]');
      expect(result.clientSecret).to.equal('[REDACTED]');
      expect(result['x-slack-signature']).to.equal('[REDACTED]');
      expect(result.url).to.equal('https://example.test/v1/chat');
    });

    it('should redact credentials nested inside request options', () => {
      const logger = new Logger({ service: 'test' });
      const sanitize = (logger as any).sanitizeForLogging.bind(logger);

      const result = sanitize({
        requestOptions: {
          headers: { authorization: 'Bearer leaked' },
          body: { password: 'hunter2', query: 'who owns the roadmap?' },
        },
      });

      expect(result.requestOptions.headers.authorization).to.equal('[REDACTED]');
      expect(result.requestOptions.body.password).to.equal('[REDACTED]');
      expect(result.requestOptions.body.query).to.equal('who owns the roadmap?');
    });

    it('should keep dates, Mongo ids and errors as their logged values', () => {
      const logger = new Logger({ service: 'test' });
      const sanitize = (logger as any).sanitizeForLogging.bind(logger);
      const orgId = new Types.ObjectId('64b7f0c2a1b2c3d4e5f60718');

      const result = sanitize({
        orgId,
        at: new Date('2026-10-13T10:00:00.000Z'),
        error: new Error('mongo down'),
      });

      expect(result.orgId).to.equal('64b7f0c2a1b2c3d4e5f60718');
      expect(result.at).to.equal('2026-10-13T10:00:00.000Z');
      expect(result.error.message).to.equal('mongo down');
      expect(result.error.stack).to.be.a('string');
    });

    it('logs binary data by its size, never its bytes', () => {
      const logger = new Logger({ service: 'test' });
      const sanitize = (logger as any).sanitizeForLogging.bind(logger);

      const result = sanitize({
        file: Buffer.from('document body'),
        nested: { bytes: new Uint8Array(4), raw: new ArrayBuffer(8) },
      });

      expect(result.file).to.equal('[binary 13 bytes]');
      expect(result.nested.bytes).to.equal('[binary 4 bytes]');
      expect(result.nested.raw).to.equal('[binary 8 bytes]');
      expect(JSON.stringify(result)).to.not.include('document body');
    });

    it('should redact secrets carried in an error message and stack', () => {
      const logger = new Logger({ service: 'test' });
      const sanitize = (logger as any).sanitizeForLogging.bind(logger);

      const result = sanitize({
        callback: new Error('exchange failed for https://idp.example/cb?code=SECRETCODE&page=2'),
        login: new Error('login failed password=hunter2'),
        upstream: new Error('401 for header Authorization: Bearer eyJhbGciOi.secretjwt'),
      });

      const logged = JSON.stringify(result);
      expect(logged).to.not.include('SECRETCODE');
      expect(logged).to.not.include('hunter2');
      expect(logged).to.not.include('secretjwt');
      expect(result.callback.message).to.include('https://idp.example/cb');
    });

    it('should keep numeric token counters readable', () => {
      const logger = new Logger({ service: 'test' });
      const sanitize = (logger as any).sanitizeForLogging.bind(logger);

      const result = sanitize({ totalTokens: 1234, tokenScopes: ['kb:write'] });
      expect(result.totalTokens).to.equal(1234);
      expect(result.tokenScopes).to.deep.equal(['kb:write']);
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

  describe('file logs and sink isolation (D3)', () => {
    let dir: string;
    let cwd: string;

    beforeEach(() => {
      dir = fs.mkdtempSync(path.join(os.tmpdir(), 'd3-logger-'));
      cwd = process.cwd();
      delete process.env.NODE_LOG_FILES;
      delete process.env.NODE_LOG_DIR;
    });

    afterEach(() => {
      process.chdir(cwd);
      fs.chmodSync(dir, 0o700);
      fs.rmSync(dir, { recursive: true, force: true });
    });

    function sinks(logger: Logger): { console: winston.transport; files: FileSink[] } {
      const transports = (logger as any).logger.transports as winston.transport[];
      return {
        console: transports.find((t) => t instanceof winston.transports.Console) as winston.transport,
        files: transports.filter((t): t is FileSink => t instanceof FileSink),
      };
    }

    // What reaches the console transport: the point a stalled pipe starves.
    function captureConsole(logger: Logger): string[] {
      const seen: string[] = [];
      sinon.stub(sinks(logger).console, 'log').callsFake((info: any, next: () => void) => {
        seen.push(String(info.message));
        next();
      });
      return seen;
    }

    async function settle(seen: string[], want: number): Promise<void> {
      for (let i = 0; i < 200 && seen.length < want; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
    }

    async function closeFiles(logger: Logger): Promise<void> {
      await Promise.all(
        sinks(logger).files.map(
          (sink) =>
            new Promise<void>((resolve) => {
              const stream = (sink as any).stream;
              if (!stream) return resolve();
              stream.once('finish', resolve);
              sink.close();
            }),
        ),
      );
    }

    it('LC-1: unset, the files are error.log and combined.log in the working directory', async () => {
      process.chdir(dir);
      expect(fileLogDirectory()).to.equal(process.cwd());
      const logger = new Logger({ service: 'lc1' });
      const files = sinks(logger).files;
      expect(files.map((f) => (f as any).filePath)).to.deep.equal([
        path.join(process.cwd(), 'error.log'),
        path.join(process.cwd(), 'combined.log'),
      ]);
      // The file sinks open their streams asynchronously; left open, they could create the files while afterEach
      // removes the directory (ENOTEMPTY in the round 19 run).
      await closeFiles(logger);
    });

    it('LC-2: NODE_LOG_FILES=false logs to the console only and creates no file', async () => {
      process.chdir(dir);
      process.env.NODE_LOG_FILES = 'false';
      expect(fileLogDirectory()).to.equal(null);
      const logger = new Logger({ service: 'lc2' });
      const seen = captureConsole(logger);
      logger.info('only the console');
      await settle(seen, 1);
      expect(sinks(logger).files).to.have.length(0);
      expect(seen).to.deep.equal(['only the console']);
      expect(fs.readdirSync(dir)).to.deep.equal([]);
    });

    it('LC-3 / LS-4: NODE_LOG_DIR holds the files, created if missing; error.log only errors', async () => {
      const logDir = path.join(dir, 'logs', 'node');
      process.env.NODE_LOG_DIR = logDir;
      expect(fileLogDirectory()).to.equal(logDir);
      const logger = new Logger({ service: 'lc3' });
      captureConsole(logger);
      logger.info('an info line');
      logger.error('an error line');
      await new Promise((resolve) => setTimeout(resolve, 20));
      await closeFiles(logger);
      const combined = fs.readFileSync(path.join(logDir, 'combined.log'), 'utf8').trim().split('\n');
      const errors = fs.readFileSync(path.join(logDir, 'error.log'), 'utf8').trim().split('\n');
      expect(combined.map((l) => JSON.parse(l).message)).to.deep.equal(['an info line', 'an error line']);
      expect(errors.map((l) => JSON.parse(l).message)).to.deep.equal(['an error line']);
    });

    it('LC-4: a malformed NODE_LOG_FILES is refused with the flag named', () => {
      process.env.NODE_LOG_FILES = 'maybe';
      expect(() => new Logger({ service: 'lc4' })).to.throw(InvalidEnvFlagError, 'NODE_LOG_FILES');
    });

    it('LS-5: an unwritable log directory never stalls the console; the failure is reported once per sink', async function () {
      if (process.getuid?.() === 0) this.skip(); // root ignores the mode bits
      fs.chmodSync(dir, 0o500);
      process.env.NODE_LOG_DIR = dir;
      const logger = new Logger({ service: 'ls5' });
      const seen = captureConsole(logger);
      for (let i = 0; i < 2000; i += 1) logger.info(`line ${i}`);
      await settle(seen, 2002);
      const lines = seen.filter((m) => m.startsWith('line '));
      expect(lines).to.have.length(2000);
      expect(lines[1999]).to.equal('line 1999');
      expect(seen.filter((m) => m === 'logger.sink_failed')).to.have.length(2);
      for (let i = 0; i < 100; i += 1) logger.info(`more ${i}`);
      await settle(seen, 2102);
      expect(seen.filter((m) => m === 'logger.sink_failed')).to.have.length(2);
      expect(sinks(logger).files.every((f) => !f.healthy)).to.equal(true);
    });

    it('LS-1: a log directory that cannot be created never stalls the console', async () => {
      const blocker = path.join(dir, 'a-file');
      fs.writeFileSync(blocker, '');
      process.env.NODE_LOG_DIR = path.join(blocker, 'logs');
      const logger = new Logger({ service: 'ls1' });
      const seen = captureConsole(logger);
      for (let i = 0; i < 2000; i += 1) logger.info(`line ${i}`);
      await settle(seen, 2002);
      expect(seen.filter((m) => m.startsWith('line '))).to.have.length(2000);
      expect(seen.filter((m) => m === 'logger.sink_failed')).to.have.length(2);
    });
  });
});
