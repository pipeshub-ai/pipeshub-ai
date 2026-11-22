import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import winston from 'winston'
import { AxiosError } from 'axios'
import { Logger, formatTextValue } from '../../../src/libs/services/logger.service'
import * as seam from '../../../src/libs/services/structured-logging'
import { bindLogContext, runWithRequestContext, setContextIdentity } from '../../../src/libs/context/request-context'
import { NotFoundError } from '../../../src/libs/errors/http.errors'
import { PgError } from '../../../src/libs/db/errors'
import { mochaHooks } from '../../helpers/logger-isolation-global'

const MESSAGE = Symbol.for('message')
const ORG = '64f0c0ffee0000000000000a'
const USER = '64f0c0ffee0000000000000b'

describe('Logger: per-module service, error fields and log context', () => {
  let log: sinon.SinonStub

  beforeEach(() => {
    ;(Logger as any).instance = null
    // No log files: the root would otherwise open error.log and combined.log in the cwd.
    sinon.stub(seam, 'getProvider').returns(
      Object.assign(new seam.DefaultLoggingProvider(), { fileLoggingEnabled: () => false }),
    )
    log = sinon.stub((Logger.getInstance() as any).logger, 'log')
  })

  afterEach(() => {
    sinon.restore()
    ;(Logger as any).instance = null
  })

  const record = (i = 0): Record<string, any> => log.getCall(i).args[0]
  const formatted = (r: Record<string, unknown>) => (Logger.getInstance() as any).logger.format.transform({ ...r }, {})
  /** The console text line, as the default console transport prints it. */
  const text = (r: Record<string, unknown>): string => String(formatted(r)[MESSAGE])
  /** The combined.log line: the file transports add json() after the same format. */
  const fileJson = (r: Record<string, unknown>): Record<string, any> =>
    JSON.parse(winston.format.json().transform(formatted(r), {})[MESSAGE])
  const inRequest = (fn: () => void) =>
    runWithRequestContext({ rootId: 'req-1' }, () => {
      setContextIdentity({ orgId: ORG, userId: USER })
      fn()
    })

  describe('one service name per caller', () => {
    it("each getInstance({service}) caller keeps its own name on its lines", () => {
      const auth = Logger.getInstance({ service: 'Auth Service Container' })
      const cm = Logger.getInstance({ service: 'ConfigurationManagerController' })
      auth.info('from auth')
      cm.info('from cm')
      expect(record(0).service).to.equal('Auth Service Container')
      expect(record(1).service).to.equal('ConfigurationManagerController')
    })

    it('a getInstance on an error path does not relabel other modules', () => {
      const auth = Logger.getInstance({ service: 'Auth Service Container' })
      Logger.getInstance({ service: 'HealthStatus' })
      auth.warn('next line')
      expect(record().service).to.equal('Auth Service Container')
    })

    it('new Logger({service}) after the root exists gets its own name', () => {
      const worker = new Logger({ service: 'CrawlingWorkerService' })
      worker.info('x')
      expect(record().service).to.equal('CrawlingWorkerService')
    })

    it('a nameless logger says default-service, also after named ones exist', () => {
      Logger.getInstance({ service: 'A' })
      Logger.getInstance().info('x')
      new Logger().info('y')
      expect([record(0).service, record(1).service]).to.deep.equal(['default-service', 'default-service'])
      expect(new Logger()).to.equal(Logger.getInstance())
    })

    it('the same name returns the same logger; every logger shares one winston and its transports', () => {
      const a = Logger.getInstance({ service: 'A' })
      const b = Logger.getInstance({ service: 'B' })
      expect(Logger.getInstance({ service: 'A' })).to.equal(a)
      expect(new Logger({ service: 'A' })).to.equal(a)
      expect(a).to.not.equal(b)
      expect(a).to.be.instanceOf(Logger)
      expect((a as any).logger).to.equal((Logger.getInstance() as any).logger)
      expect((b as any).logger).to.equal((a as any).logger)
    })

    it('stubs on Logger.prototype and on the root instance still see a named logger', () => {
      const a = Logger.getInstance({ service: 'A' })
      const proto = sinon.stub(Logger.prototype, 'warn')
      const root = sinon.stub(Logger.getInstance(), 'error')
      a.warn('w')
      a.error('e')
      expect(proto.calledOn(a)).to.equal(true)
      expect(root.calledOnceWith('e')).to.equal(true)
    })

    it('metadata added to one logger does not reach another', () => {
      const a = Logger.getInstance({ service: 'A', additionalMeta: { component: 'x' } })
      Logger.getInstance({ service: 'B' }).info('b')
      a.info('a')
      expect(record(0)).to.not.have.property('component')
      expect(record(1)).to.include({ service: 'A', component: 'x' })
    })

    it('a reset root brings fresh named loggers', () => {
      const a = Logger.getInstance({ service: 'A' })
      ;(Logger as any).instance = null
      expect(Logger.getInstance({ service: 'A' })).to.not.equal(a)
    })

    it("a named logger's line names the caller's file, not the logger", () => {
      Logger.getInstance({ service: 'A' }).info('x')
      expect(record().filename).to.equal('logger.module-loggers.test.ts')
    })

    it('a line below the level is skipped for a named logger too', () => {
      Logger.getInstance({ service: 'A' }).debug('hidden')
      expect(log.called).to.equal(false)
    })

    it('the isolation hook reports a plain assignment on a named logger', () => {
      const a = Logger.getInstance({ service: 'A' })
      ;(a as any).warn = () => undefined
      mochaHooks.afterEach.call({ currentTest: { fullTitle: () => 'assigning test' } } as any)
      expect(Object.prototype.hasOwnProperty.call(a, 'warn')).to.equal(false)
      expect(() => mochaHooks.afterAll()).to.throw(/"assigning test" left warn replaced/)
    })
  })

  describe('text line', () => {
    it('shows the service in the second slot, and no suffix outside a request', () => {
      Logger.getInstance({ service: 'Auth Service Container' }).info('hello')
      expect(text(record())).to.match(/^\S+ \S+ - Auth Service Container - INFO - \[logger\.module-loggers\.test\.ts:\d+\] - hello$/)
    })

    it('appends org_id, user_id, then bound ids in a fixed order', () => {
      inRequest(() => {
        bindLogContext({ tool: 'search', conversation_id: 'c1', kb_id: 'kb-1' })
        Logger.getInstance({ service: 'KB' }).info('done')
      })
      expect(text(record())).to.match(
        new RegExp(`\\[req:req-1 thr:\\d+\\] \\[logger\\.module-loggers\\.test\\.ts:\\d+\\] - done \\| org_id=${ORG} user_id=${USER} conversation_id=c1 kb_id=kb-1 tool=search$`),
      )
    })

    it('a bound value cannot start a new line or a fake field', () => {
      inRequest(() => {
        bindLogContext({ kb_id: 'kb1\n2026-01-01 - x - ERROR - forged | user_id=other' })
        Logger.getInstance({ service: 'KB' }).info('done')
      })
      const line = text(record())
      expect(line.split('\n')).to.have.length(1)
      expect(line.endsWith(` | org_id=${ORG} user_id=${USER} kb_id="kb1\\n2026-01-01 - x - ERROR - forged | user_id=other"`)).to.equal(true)
    })

    it('null and undefined fields are left out', () => {
      expect(text({ level: 'info', message: 'm', service: 'S', org_id: null, user_id: undefined })).to.not.contain(' | ')
    })

    // The shared table from docs/logging.md ("Field values in text"), identical in both runtimes: input -> text,
    // null meaning the field is left out of the line.
    const VECTORS: Array<[string | number | boolean | null, string | null]> = [
      ["c1", "c1"],
      ["65f0a1b2c3d4e5f6a7b8c9d0", "65f0a1b2c3d4e5f6a7b8c9d0"],
      ["sync_run.end", "sync_run.end"],
      ["rate_limited", "rate_limited"],
      ["web_search", "web_search"],
      ["job:kh_restamp", "job:kh_restamp"],
      ["_", "_"],
      ["a/b:c@d-e_f", "a/b:c@d-e_f"],
      ["ok two", "\"ok two\""],
      ["", "\"\""],
      ["a,b", "\"a,b\""],
      ["a+b", "\"a+b\""],
      ["it's", "\"it's\""],
      ["ü", "\"ü\""],
      ["x#1", "\"x#1\""],
      ["(1)", "\"(1)\""],
      ["a\"b", "\"a\\\"b\""],
      ["a=b", "\"a=b\""],
      ["a|b", "\"a|b\""],
      ["a\\b", "\"a\\\\b\""],
      ["line1\nline2", "\"line1\\nline2\""],
      ["tab\there", "\"tab\\there\""],
      ["\u0001", "\"\\u0001\""],
      [12, "12"],
      [-3, "-3"],
      [0, "0"],
      [true, "true"],
      [false, "false"],
      [null, null],
    ]
    for (const [input, output] of VECTORS) {
      it(`renders ${JSON.stringify(input)} as ${output ?? 'nothing'}`, () => {
        const line = text({ level: 'info', message: 'm', service: 'S', kb_id: input })
        if (output === null) {
          expect(line).to.not.contain(' | ')
        } else {
          expect(formatTextValue(input as string | number | boolean)).to.equal(output)
          expect(line.endsWith(` - m | kb_id=${output}`)).to.equal(true)
        }
      })
    }
  })

  describe('JSON file line', () => {
    it('has the service, identity and bound ids as top-level snake_case keys', () => {
      inRequest(() => {
        bindLogContext({ kb_id: 'kb-1' })
        Logger.getInstance({ service: 'Knowledge Base Controller' }).error('Error getting KB', { error: new NotFoundError('kb missing') })
      })
      const line = fileJson(record())
      expect(line).to.include({
        level: 'error',
        message: 'Error getting KB',
        service: 'Knowledge Base Controller',
        request_id: 'req-1',
        org_id: ORG,
        user_id: USER,
        kb_id: 'kb-1',
        filename: 'logger.module-loggers.test.ts',
      })
      expect(line).to.have.property('timestamp')
      expect(line.metadata.error).to.include({ name: 'NotFoundError', statusCode: 404 })
      // Added by winston itself, on every line, and never `service`: that would beat each logger's own name.
      expect((Logger.getInstance() as any).logger.defaultMeta).to.deep.equal({ service_tier: 'nodejs_service' })
    })
  })

  describe('error serialisation', () => {
    const errorOf = (meta: unknown) => {
      Logger.getInstance({ service: 'S' }).error('failed', meta)
      return log.lastCall.args[0].metadata
    }

    it('a BaseError keeps name, code, statusCode, message, redacted metadata, stack and cause', () => {
      const err = new NotFoundError('kb missing', { kbId: 'kb1', password: 'p' })
      ;(err as any).cause = new Error('row not found')
      const e = errorOf({ error: err }).error
      expect(e).to.include({ name: 'NotFoundError', code: err.code, statusCode: 404, message: 'kb missing' })
      expect(e.code).to.be.a('string').and.not.equal('')
      expect(e.metadata).to.deep.equal({ kbId: 'kb1', password: '[REDACTED]' })
      expect(e.stack).to.match(/^NotFoundError: kb missing/)
      expect(e.cause).to.include({ name: 'Error', message: 'row not found' })
      expect(e).to.not.have.property('timestamp')
    })

    it('a generic Error keeps a string or numeric code and its cause chain', () => {
      const inner = Object.assign(new Error('E11000 duplicate key'), { code: 11000 })
      const outer = Object.assign(new Error('save failed', { cause: inner }), { code: 'E_WRITE' })
      const e = errorOf({ error: outer }).error
      expect(e.code).to.equal('E_WRITE')
      expect(e.cause).to.include({ message: 'E11000 duplicate key', code: 11000 })
    })

    it('drops a non-scalar code and adds no cause key when there is none', () => {
      const e = errorOf({ error: Object.assign(new Error('x'), { code: { nested: true } }) }).error
      expect(e).to.not.have.any.keys('code', 'cause')
    })

    it('is cycle-safe through cause', () => {
      const a: any = new Error('a')
      const b: any = new Error('b', { cause: a })
      a.cause = b
      const e = errorOf({ error: a }).error
      expect(e.cause.message).to.equal('b')
      expect(e.cause.cause).to.equal('[Circular]')
    })

    it('a non-Error cause is kept and still redacted by key', () => {
      expect(errorOf({ error: new Error('x', { cause: 'socket hang up' }) }).error.cause).to.equal('socket hang up')
      expect(errorOf({ error: new Error('x', { cause: { reason: 'r', apiKey: 'k' } }) }).error.cause).to.deep.equal({
        reason: 'r',
        apiKey: '[REDACTED]',
      })
    })

    it('an error passed bare as the metadata gets the same fields', () => {
      const err = new NotFoundError('nf')
      expect(errorOf(err)).to.include({ name: 'NotFoundError', code: err.code, statusCode: 404 })
    })

    it('redacts a secret in the message and stack', () => {
      const e = errorOf({ error: new Error('call failed: Bearer abc.def.ghi') }).error
      expect(JSON.stringify(e)).to.not.contain('abc.def.ghi')
    })

    it('an AxiosError keeps status, method, url and the OAuth code, never config, headers or bodies', () => {
      const cfg: any = {
        url: 'https://oauth2.googleapis.com/token?code=SECRET-CODE',
        method: 'post',
        headers: { Authorization: 'Bearer AT-SECRET' },
        data: JSON.stringify({ refresh_token: 'RT-SECRET-123', client_secret: 'CS-SECRET-456' }),
      }
      const err = new AxiosError('Request failed with status code 400', 'ERR_BAD_REQUEST', cfg, { _header: 'POST /token Authorization: Bearer AT-SECRET' }, {
        status: 400, statusText: 'Bad Request', data: { error: 'invalid_grant', echoed: 'RT-SECRET-123' }, headers: {}, config: cfg,
      } as any)
      inRequest(() => Logger.getInstance({ service: 'S' }).error('token refresh failed', { error: err }))
      const e = record().metadata.error
      expect(e).to.include({ name: 'AxiosError', code: 'ERR_BAD_REQUEST', status: 400, method: 'post', upstreamError: 'invalid_grant' })
      expect(e.url).to.match(/^https:\/\/oauth2\.googleapis\.com\/token\?code=/)
      const rendered = JSON.stringify(fileJson(record())) + text(record())
      for (const secret of ['RT-SECRET-123', 'CS-SECRET-456', 'AT-SECRET', 'SECRET-CODE']) {
        expect(rendered).to.not.contain(secret)
      }
    })

    it('a network AxiosError keeps its cause and has no status', () => {
      const refused = Object.assign(new Error('connect ECONNREFUSED 10.0.0.1:8088'), { code: 'ECONNREFUSED' })
      const err = AxiosError.from(refused, 'ECONNREFUSED', { url: 'http://connector:8088/x', method: 'get', data: '{"token":"T-SECRET"}' } as any)
      const e = errorOf({ error: err }).error
      expect(e).to.include({ code: 'ECONNREFUSED', method: 'get', url: 'http://connector:8088/x' })
      expect(e.status).to.equal(undefined)
      expect(e.cause).to.include({ code: 'ECONNREFUSED' })
      expect(JSON.stringify(e)).to.not.contain('T-SECRET')
    })

    it("a PgError's driver cause keeps its sqlstate code but not its detail", () => {
      const driver = Object.assign(new Error('duplicate key value violates unique constraint "users_email"'), {
        code: '23505',
        detail: 'Key (email)=(a@b.c) already exists.',
      })
      const err = new PgError('UNIQUE_VIOLATION', 'duplicate', 409, '23505', driver)
      const e = errorOf({ error: err }).error
      expect(e).to.include({ name: 'PgError', code: err.code, statusCode: 409 })
      expect(e.cause).to.include({ code: '23505' })
      expect(JSON.stringify(e)).to.not.contain('a@b.c')
    })

    it('a deep cause chain is cut at the metadata depth limit', () => {
      let err: Error = new Error('root cause')
      for (let i = 0; i < 12; i++) err = new Error(`layer ${i}`, { cause: err })
      Logger.getInstance({ service: 'S' }).error('deep', { error: err })
      const line = JSON.stringify(fileJson(record()))
      expect(line).to.contain('[Truncated]')
      expect(line).to.not.contain('root cause')
    })
  })
})
