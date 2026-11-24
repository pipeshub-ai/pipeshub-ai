import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import winston from 'winston';
import type { NextFunction, Response } from 'express';
import { getKafkaConfig } from '../../../../src/modules/configuration_manager/controller/cm_controller';
import { loadConfigurationManagerConfig } from '../../../../src/modules/configuration_manager/config/config';
import { configPaths } from '../../../../src/modules/configuration_manager/paths/paths';
import { EncryptionService } from '../../../../src/libs/encryptor/encryptor';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { Logger } from '../../../../src/libs/services/logger.service';
import type { AuthenticatedUserRequest } from '../../../../src/libs/middlewares/types';
import type { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service';
import { captureLogRecords, type LogRecord } from '../../../helpers/log-capture';

// N5l: a stored setting that decrypts cleanly but is not JSON. V8's SyntaxError quotes the text it could not
// read (all of it up to 20 characters, else 10 around the error), so the start of a secret reached the log.
// The real handler, real encryption, the real error middleware, the real console formats and the log files' format;
// only the store is faked.

const MESSAGE = Symbol.for('message');

describe('Configuration manager: a stored setting that is not JSON never puts its text in a log line', () => {
  let savedSecretKey: string | undefined;
  let records: LogRecord[];
  const values = new Map<string, string>();
  const store = { get: async (key: string) => values.get(key) ?? null } as unknown as KeyValueStoreService;
  const root = () => Logger.getInstance() as unknown as { logger: winston.Logger };

  const seal = (plaintext: string): string => {
    const cfg = loadConfigurationManagerConfig();
    return EncryptionService.getInstance(cfg.algorithm, cfg.secretKey).encrypt(plaintext);
  };

  // What an operator reads: each record through the console format (text and LOG_FORMAT=json), and as error.log and
  // combined.log write it (their transports' format in initializeLogger: the text format, then winston's json()).
  const rendered = (record: LogRecord, format: 'text' | 'json' | 'file'): string => {
    process.env.LOG_FORMAT = format === 'json' ? 'json' : 'text';
    Logger.applyEnv();
    const output =
      format === 'file'
        ? winston.format.combine(root().logger.format, winston.format.json())
        : root().logger.transports.find((t) => t instanceof winston.transports.Console)!.format!;
    const shaped = root().logger.format.transform({ ...record }, {}) as Record<string | symbol, unknown>;
    return String((output.transform(shaped, {}) as Record<symbol, unknown>)[MESSAGE]);
  };
  const everyOutput = (record: LogRecord): string[] => [
    JSON.stringify(record),
    rendered(record, 'text'),
    rendered(record, 'json'),
    rendered(record, 'file'),
  ];

  /** Runs GET kafka config over `plaintext` stored encrypted, then the error middleware, as Express would. */
  const readStored = async (plaintext: string) => {
    values.set(configPaths.broker.kafka, seal(plaintext));
    records.length = 0;
    const res: any = { headersSent: false, status: sinon.stub(), json: sinon.stub(), end: sinon.stub(), setHeader: sinon.stub() };
    res.status.returns(res);
    res.json.returns(res);
    const next = sinon.stub();
    await getKafkaConfig(store)({} as AuthenticatedUserRequest, res as Response, next as NextFunction);
    expect(next.calledOnce, 'the handler passed the failure on').to.equal(true);
    ErrorMiddleware.handleError()(next.firstCall.args[0], { method: 'GET', params: {} } as any, res, sinon.stub());
    const lines = records.flatMap(everyOutput);
    const body = JSON.stringify(res.json.firstCall?.args[0]);
    return { lines, body, records: [...records] };
  };

  const expectNotIn = (texts: string[], secret: string) => {
    for (const text of texts) expect(text, `leaked ${secret}`).to.not.include(secret);
  };

  before(() => {
    savedSecretKey = process.env.SECRET_KEY;
    process.env.SECRET_KEY = '5a50e50915d9817fd6e66954d113b89f74a23980a765b226ff1256516adda3d7';
  });
  after(() => {
    if (savedSecretKey === undefined) delete process.env.SECRET_KEY;
    else process.env.SECRET_KEY = savedSecretKey;
  });
  beforeEach(() => {
    records = captureLogRecords();
  });
  afterEach(() => {
    sinon.restore();
    delete process.env.LOG_FORMAT;
    Logger.applyEnv();
  });

  it('a short value (quoted whole by V8) is not in any line', async () => {
    const secret = 'FAKE-pw-0123456';
    const { lines, records: written } = await readStored(secret);
    // The failure is still logged once by the middleware, as a SyntaxError: the fix must not drop the line.
    const failure = written.find((r) => r.message === 'http.request.error');
    expect(failure, 'the middleware line').to.not.equal(undefined);
    expect(failure!.error_type).to.equal('SyntaxError');
    // The middleware's line first: the handler logs nothing of its own.
    expectNotIn(everyOutput(failure!), secret);
    expectNotIn(everyOutput(failure!), "token 'F'");
    expectNotIn(lines, secret);
  });

  it('a long value: its first 10 characters (what V8 quotes) are not in any line', async () => {
    const secret = 'sk-FAKE0123456789abcdefghijklmnopqrstuvwxyz';
    const { lines } = await readStored(secret);
    expectNotIn(lines, secret.slice(0, 10));
  });

  it('a JSON-shaped value broken at a secret: the 10 characters around the error are not in any line', async () => {
    const { lines } = await readStored('{"brokers":["kafka:9092"],"sasl":{"password": FAKEPW0123456789}}');
    expectNotIn(lines, 'FAKEPW');
  });

  it('the response body never carried it (control: passes on main)', async () => {
    const secret = 'FAKE-pw-0123456';
    const { body } = await readStored(secret);
    expect(body).to.include('INTERNAL_ERROR');
    expect(body).to.not.include(secret);
  });

  it('a JSON.parse message passed on as a string (`{ error: e.message }`) is not in the line', () => {
    // Four readers log `error.message` as a string (metrics collection config, MCP access policy, two migrations).
    const secret = 'FAKE-pw-0123456';
    let message = '';
    try {
      JSON.parse(secret);
    } catch (e) {
      message = (e as Error).message;
    }
    records.length = 0;
    Logger.getInstance({ service: 'N5lRepro' }).warn('Could not read the stored state', { error: message });
    expect(records).to.have.length(1);
    expectNotIn(everyOutput(records[0]), secret);
  });
});
