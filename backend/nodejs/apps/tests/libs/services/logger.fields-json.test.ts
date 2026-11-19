import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import winston from 'winston'
import { Logger, getLogFormat } from '../../../src/libs/services/logger.service'
import * as seam from '../../../src/libs/services/structured-logging'
import { bindLogContext, runWithRequestContext, setContextIdentity } from '../../../src/libs/context/request-context'
import { NotFoundError } from '../../../src/libs/errors/http.errors'
import { withCause } from '../../../src/libs/errors/base.error'

const MESSAGE = Symbol.for('message')
const ORG = '64f0c0ffee0000000000000a'
const USER = '64f0c0ffee0000000000000b'
const OTHER_ORG = '64f0c0ffee0000000000000c'

describe('Logger: call-site fields, the stack rule and LOG_FORMAT=json', () => {
  let log: sinon.SinonStub
  let provider: seam.StructuredLoggingProvider

  const freshRoot = (format?: string): void => {
    if (format === undefined) delete process.env.LOG_FORMAT
    else process.env.LOG_FORMAT = format
    ;(Logger as any).instance = null
    log = sinon.stub((Logger.getInstance() as any).logger, 'log')
  }

  beforeEach(() => {
    provider = Object.assign(new seam.DefaultLoggingProvider(), { fileLoggingEnabled: () => false })
    sinon.stub(seam, 'getProvider').callsFake(() => provider)
    freshRoot()
  })

  afterEach(() => {
    sinon.restore()
    delete process.env.LOG_FORMAT
    ;(Logger as any).instance = null
  })

  const record = (i = 0): Record<string, any> => log.getCall(i).args[0]
  const winstonRoot = (): winston.Logger => (Logger.getInstance() as any).logger
  /** What the console transport prints for a record, under the current LOG_FORMAT. */
  const consoleLine = (r: Record<string, unknown>): string => {
    const shaped = winstonRoot().format.transform({ ...r }, {}) as Record<string | symbol, unknown>
    const console = winstonRoot().transports.find((t) => t instanceof winston.transports.Console)!
    return String((console.format!.transform(shaped, {}) as Record<symbol, unknown>)[MESSAGE])
  }
  const inRequest = (fn: () => void) =>
    runWithRequestContext({ rootId: 'req-1' }, () => {
      setContextIdentity({ orgId: ORG, userId: USER })
      fn()
    })

  describe('call-site fields', () => {
    it('a field of the right type becomes a field; the rest stays metadata', () => {
      Logger.getInstance({ service: 'X' }).info('sync_run.end', {
        status: 200, duration_ms: 12, outcome: 'success', kbId: 'k', nested: { a: 1 },
      })
      expect(record()).to.include({ status: 200, duration_ms: 12, outcome: 'success' })
      expect(record().metadata).to.deep.equal({ kbId: 'k', nested: { a: 1 } })
      expect(consoleLine(record())).to.include('- sync_run.end | status=200 duration_ms=12 outcome=success')
    })

    it('a value of the wrong type stays in metadata, so a field always means the same thing', () => {
      Logger.getInstance().info('x', {
        status: 'healthy', reason: 'Request failed: upstream said no', method: 'S256', outcome: 'snapshot_failed', target: 'John Doe',
      })
      expect(record()).to.not.have.any.keys('status', 'reason', 'method', 'outcome', 'target')
      expect(record().metadata).to.include({ status: 'healthy', method: 'S256' })
      expect(consoleLine(record())).to.not.include(' | ')
    })

    it('no metadata is left when every key became a field; undefined values are dropped', () => {
      Logger.getInstance().info('x', { status: 404, error_class: undefined })
      expect(record().metadata).to.equal(undefined)
      expect(record()).to.not.have.property('error_class')
    })

    it("never reads Object.prototype: keys like valueOf or constructor neither throw nor become fields, in text or JSON", () => {
      const meta = { hasOwnProperty: 'v', valueOf: 1, propertyIsEnumerable: 'p', toLocaleString: 'l', toString: 'abc', constructor: 'k' }
      for (const format of [undefined, 'json']) {
        freshRoot(format)
        expect(() => Logger.getInstance().info('payload', { ...meta })).to.not.throw()
        expect(Object.keys(record())).to.not.include.members(Object.keys(meta))
        expect(record().metadata).to.deep.equal(meta)
        expect(() => consoleLine(record())).to.not.throw()
      }
    })

    it('cannot replace the standard keys', () => {
      Logger.getInstance({ service: 'S' }).info('real', { level: 'error', message: 'fake', service: 'evil', timestamp: 'x', filename: 'f', line: 1 })
      expect(record()).to.include({ level: 'info', message: 'real', service: 'S' })
      expect(record().metadata).to.include({ level: 'error', message: 'fake' })
    })

    it('an explicit field beats a bound one; the identity is never replaced', () => {
      inRequest(() => {
        bindLogContext({ kb_id: 'bound' })
        Logger.getInstance().info('x', { kb_id: 'explicit', org_id: OTHER_ORG, user_id: OTHER_ORG })
      })
      expect(record()).to.include({ kb_id: 'explicit', org_id: ORG, user_id: USER })
    })

    it('outside a request an explicit org fills the identity, shown the way the provider shows ids', () => {
      Logger.getInstance().info('x', { org_id: ORG })
      expect(record().org_id).to.equal(ORG)
      provider = Object.assign(new seam.DefaultLoggingProvider(), {
        fileLoggingEnabled: () => false,
        logIdentity: undefined,
        hashOrgId: (id: string) => `h-${id.slice(-4)}`,
      })
      Logger.getInstance().info('y', { org_id: ORG, user_id: USER })
      expect(record(1)).to.include({ organization_id_hash: 'h-000a' })
      expect(record(1)).to.not.have.any.keys('org_id', 'user_id')
    })

    it('an org that is not an id never becomes the identity', () => {
      Logger.getInstance().info('x', { org_id: 'ceo@example.com' })
      expect(record()).to.not.have.property('org_id')
      expect(record().metadata).to.deep.equal({ org_id: 'ceo@example.com' })
    })

    it('event_name is on the record for a provider but printed by neither format', () => {
      Logger.getInstance().info('http.request.end', { event_name: 'http.request.end' })
      expect(record().event_name).to.equal('http.request.end')
      expect(consoleLine(record())).to.not.include('event_name')
      freshRoot('json')
      Logger.getInstance().info('http.request.end', { event_name: 'http.request.end' })
      expect(JSON.parse(consoleLine(record()))).to.not.have.property('event_name')
    })
  })

  describe('stack only at ERROR', () => {
    const failure = () => withCause(new NotFoundError('kb missing'), new Error('driver said no'))

    it('a WARN line says why without where, causes included', () => {
      Logger.getInstance().warn('x', { error: failure() })
      const error = record().metadata.error
      expect(error).to.include({ name: 'NotFoundError', message: 'kb missing' })
      expect(error).to.not.have.property('stack')
      expect(error.cause).to.include({ message: 'driver said no' }).and.not.have.property('stack')
    })

    it('an ERROR line keeps the stack and the cause\'s stack', () => {
      Logger.getInstance().error('x', { error: failure() })
      expect(record().metadata.error.stack).to.be.a('string')
      expect(record().metadata.error.cause.stack).to.be.a('string')
    })

    it("keeps another client's integer status (gaxios, http-errors)", () => {
      Logger.getInstance().error('x', Object.assign(new Error('upstream'), { status: 403 }))
      expect(record().metadata).to.include({ name: 'Error', status: 403 })
    })
  })

  describe('LOG_FORMAT', () => {
    it('reads text or json, any case; anything else is text', () => {
      for (const [value, expected] of [[undefined, 'text'], ['text', 'text'], ['JSON', 'json'], [' json ', 'json'], ['yaml', 'text']] as const) {
        if (value === undefined) delete process.env.LOG_FORMAT
        else process.env.LOG_FORMAT = value
        expect(getLogFormat()).to.equal(expected)
      }
    })

    it('text stays the default line', () => {
      Logger.getInstance({ service: 'X' }).info('hello')
      expect(consoleLine(record())).to.match(/ - X - INFO - .* - hello$/)
    })

    it('json prints one object per line, with the Python keys in order and identity first', () => {
      freshRoot('json')
      inRequest(() => {
        bindLogContext({ kb_id: 'kb1' })
        Logger.getInstance({ service: 'HttpAccess' }).warn('http.request.end', {
          method: 'GET', route: '/api/v1/kb/:kbId', status: 503, duration_ms: 4, error_class: 'unavailable', target: null,
        })
      })
      const line = consoleLine(record())
      expect(line.split('\n')).to.have.length(1)
      const json = JSON.parse(line)
      expect(Object.keys(json)).to.deep.equal([
        'timestamp', 'level', 'logger', 'filename', 'line', 'message', 'request_id',
        'org_id', 'user_id', 'thread_id', 'kb_id', 'method', 'route', 'status', 'duration_ms', 'error_class', 'target',
      ])
      expect(json).to.include({ level: 'warning', logger: 'HttpAccess', request_id: 'req-1', org_id: ORG, status: 503, target: null })
      expect(json.timestamp).to.match(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/)
      expect(json).to.not.have.property('service_tier')
    })

    it('json uses the level names error, warning, info and debug', () => {
      freshRoot('json')
      const logger = Logger.getInstance()
      logger.error('a')
      logger.warn('b')
      logger.info('c')
      expect([0, 1, 2].map((i) => JSON.parse(consoleLine(record(i))).level)).to.deep.equal(['error', 'warning', 'info'])
      expect(JSON.parse(consoleLine(record(2))).request_id).to.equal(null)
    })

    it('json keeps a serialised error at the top and everything else under metadata', () => {
      freshRoot('json')
      Logger.getInstance().error('x', { error: new NotFoundError('kb missing'), kbId: 'k1', org_id: 'not-an-id', nested: { a: 1 } })
      const json = JSON.parse(consoleLine(record()))
      expect(json.error).to.include({ name: 'NotFoundError', message: 'kb missing', statusCode: 404 })
      expect(json.metadata).to.deep.equal({ kbId: 'k1', org_id: 'not-an-id', nested: { a: 1 } })
      expect(json).to.not.have.property('org_id')
      expect(json).to.not.have.property('kbId')
    })

    it('json keeps a string error under metadata', () => {
      freshRoot('json')
      Logger.getInstance().error('x', { error: 'just text' })
      const json = JSON.parse(consoleLine(record()))
      expect(json).to.not.have.property('error')
      expect(json.metadata).to.deep.equal({ error: 'just text' })
    })

    it('json redacts every string, the message included, and stays one line', () => {
      freshRoot('json')
      const token = 'Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLXRoYXQtaXMtbG9uZw'
      Logger.getInstance().error(`call failed with ${token}\nsecond line`, { note: `header was ${token}`, list: [token] })
      const line = consoleLine(record())
      expect(line).to.not.include('eyJhbGciOiJIUzI1NiJ9')
      expect(line.split('\n')).to.have.length(1)
      expect(JSON.parse(line).message).to.include('second line')
    })

    it("a provider's console format still wins, and is offered the json default", () => {
      const seen: unknown[] = []
      const own = winston.format.printf(() => 'provider line')
      provider = Object.assign(new seam.DefaultLoggingProvider(), {
        fileLoggingEnabled: () => false,
        consoleFormat: (fmt: unknown) => {
          seen.push(fmt)
          return own
        },
      })
      freshRoot('json')
      Logger.getInstance().info('x')
      expect(consoleLine(record())).to.equal('provider line')
      expect(seen).to.have.length(1)
    })
  })

  describe('applyEnv (after dotenv loaded .env)', () => {
    afterEach(() => {
      delete process.env.LOG_LEVEL
    })

    it('switches the console to json and the level to debug without a new root', () => {
      const root = Logger.getInstance()
      Logger.getInstance().info('before')
      expect(() => JSON.parse(consoleLine(record()))).to.throw()
      process.env.LOG_FORMAT = 'json'
      process.env.LOG_LEVEL = 'debug'
      Logger.applyEnv()
      expect(Logger.getInstance()).to.equal(root)
      expect(winstonRoot().level).to.equal('debug')
      Logger.getInstance().debug('after')
      expect(JSON.parse(consoleLine(record(1))).message).to.equal('after')
    })

    it('warns once about an unknown LOG_FORMAT and keeps text', () => {
      process.env.LOG_FORMAT = 'yaml'
      Logger.applyEnv()
      const warnings = log.getCalls().filter((c) => c.args[0].message === 'Unknown LOG_FORMAT, using text')
      expect(warnings).to.have.length(1)
      expect(warnings[0]!.args[0].metadata).to.deep.equal({ value: 'yaml' })
      Logger.getInstance().info('x')
      expect(consoleLine(record(1))).to.match(/ - INFO - /)
    })
  })
})
