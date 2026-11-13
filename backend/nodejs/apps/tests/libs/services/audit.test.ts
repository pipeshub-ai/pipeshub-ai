import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import winston from 'winston'
import { audit, auditRefusal, refusal, refusalReason } from '../../../src/libs/services/audit'
import { Logger } from '../../../src/libs/services/logger.service'
import * as seam from '../../../src/libs/services/structured-logging'
import { runWithRequestContext, setContextIdentity } from '../../../src/libs/context/request-context'

const MESSAGE = Symbol.for('message')
const ORG = '64f0c0ffee0000000000000a'
const USER = '64f0c0ffee0000000000000b'
const TARGET = '64f0c0ffee0000000000000c'

describe('audit(): one fixed-shape line per security event', () => {
  let log: sinon.SinonStub
  let provider: seam.StructuredLoggingProvider

  const freshRoot = (format?: string) => {
    if (format) process.env.LOG_FORMAT = format
    else delete process.env.LOG_FORMAT
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

  const record = (): Record<string, any> => log.lastCall.args[0]
  const consoleLine = (r: Record<string, unknown>): string => {
    const root = (Logger.getInstance() as any).logger as winston.Logger
    const shaped = root.format.transform({ ...r }, {}) as Record<string | symbol, unknown>
    const console = root.transports.find((t) => t instanceof winston.transports.Console)!
    return String((console.format!.transform(shaped, {}) as Record<symbol, unknown>)[MESSAGE])
  }
  const inRequest = (fn: () => void) =>
    runWithRequestContext({ rootId: 'req-1' }, () => {
      setContextIdentity({ orgId: ORG, userId: USER })
      fn()
    })

  it('is an INFO line on the audit logger, named by the event, with the caller as its location', () => {
    inRequest(() => audit('user.role_change', { target: TARGET, targetIsUser: true, outcome: 'success', reason: 'admin_granted' }))
    expect(record()).to.include({
      level: 'info', service: 'audit', message: 'user.role_change', org_id: ORG, user_id: USER,
      actor: USER, target: TARGET, outcome: 'success', reason: 'admin_granted', event_name: 'user.role_change',
    })
    expect(record().filename).to.equal('audit.test.ts')
    expect(consoleLine(record())).to.include(' - audit - INFO - ').and.include(
      `user.role_change | org_id=${ORG} user_id=${USER} outcome=success actor=${USER} target=${TARGET} reason=admin_granted`,
    )
  })

  it('always has the same keys: missing ones are null in JSON and left out of the text', () => {
    inRequest(() => audit('auth.sign_out', { outcome: 'success' }))
    expect(record()).to.include({ target: null, reason: null })
    expect(consoleLine(record())).to.not.include('target=').and.not.include('reason=')
    freshRoot('json')
    inRequest(() => audit('auth.sign_out', { outcome: 'success' }))
    const json = JSON.parse(consoleLine(record()))
    expect(json).to.include({ logger: 'audit', message: 'auth.sign_out', actor: USER, target: null, outcome: 'success', reason: null })
  })

  it('before sign-in, takes the actor and org it is given; a request org always wins', () => {
    audit('auth.sign_in', { actor: USER, org: ORG, outcome: 'failure', reason: 'bad_password' })
    expect(record()).to.include({ actor: USER, org_id: ORG, outcome: 'failure', reason: 'bad_password' })
    inRequest(() => audit('auth.sign_in', { actor: TARGET, org: TARGET, outcome: 'success' }))
    expect(record()).to.include({ org_id: ORG, actor: TARGET })
  })

  it('rejects an actor, target or org that is not a PipesHub id: no part of an email or a name reaches any sink', () => {
    for (const [fields, leaked] of [
      [{ actor: 'john.doe@example.com' }, 'john.doe'],
      [{ target: 'Jane Smith' }, 'Jane'],
      [{ org: 'acme@corp.com' }, 'acme'],
    ] as const) {
      audit('user.delete', { ...fields, outcome: 'failure', reason: 'not_allowed' })
      expect(record().reason).to.equal('invalid_id')
      for (const format of [undefined, 'json']) {
        freshRoot(format)
        audit('user.delete', { ...fields, outcome: 'failure' })
        expect(JSON.stringify(record())).to.not.include(leaked)
        expect(consoleLine(record())).to.not.include(leaked)
      }
    }
  })

  it('a reason that is not a snake_case code is other; a bad event name is audit.invalid_event', () => {
    audit('user.update', { actor: USER, outcome: 'failure', reason: 'Failed for ceo@example.com' })
    expect(record().reason).to.equal('other')
    audit('Login Attempt by bob', { actor: USER, outcome: 'success' })
    expect(record().message).to.equal('audit.invalid_event')
    expect(JSON.stringify(record())).to.not.include('bob')
  })

  it('shows user ids the way the provider shows them: hashed, or hidden by a legacy provider', () => {
    provider = Object.assign(new seam.DefaultLoggingProvider(), {
      fileLoggingEnabled: () => false,
      logIdentity: (ids: seam.LogIdentity) => ({ ...(ids.user_id && { user_id: `u-${ids.user_id.slice(-4)}` }) }),
    })
    inRequest(() => audit('user.role_change', { target: TARGET, targetIsUser: true, outcome: 'success' }))
    expect(record()).to.include({ actor: 'u-000b', target: 'u-000c' })
    expect(JSON.stringify(record())).to.not.include(USER)

    provider = Object.assign(new seam.DefaultLoggingProvider(), {
      fileLoggingEnabled: () => false,
      logIdentity: undefined,
      hashOrgId: (id: string) => `h-${id.slice(-4)}`,
    })
    inRequest(() => audit('user.role_change', { target: TARGET, targetIsUser: true, outcome: 'success' }))
    expect(record()).to.include({ actor: null, target: null, organization_id_hash: 'h-000a' })
    expect(JSON.stringify(record())).to.not.include(USER).and.not.include(TARGET).and.not.include(ORG)
  })

  it('a target that is not a user is shown as it is', () => {
    provider = Object.assign(new seam.DefaultLoggingProvider(), {
      fileLoggingEnabled: () => false,
      logIdentity: () => ({}),
    })
    audit('config.update', { actor: USER, target: TARGET, outcome: 'success' })
    expect(record()).to.include({ actor: null, target: TARGET })
  })

  it('shows a known sign-in method as auth_method, after reason, in text and JSON', () => {
    audit('auth.sign_in', { actor: USER, org: ORG, outcome: 'success', authMethod: 'password' })
    expect(record()).to.include({ auth_method: 'password' })
    expect(consoleLine(record())).to.match(/actor=\S+ auth_method=password$/)
    freshRoot('json')
    audit('auth.sign_in', { actor: USER, org: ORG, outcome: 'success', authMethod: 'google' })
    expect(JSON.parse(consoleLine(record()))).to.include({ auth_method: 'google' })
  })

  it('shows any other method as other: an email or free text never reaches a field or metadata', () => {
    for (const authMethod of ['ceo@example.com', 'pass word', 'github2', 'SAMLSSO']) {
      for (const format of [undefined, 'json']) {
        freshRoot(format)
        audit('auth.sign_in', { org: ORG, outcome: 'failure', reason: 'method_not_allowed', authMethod })
        expect(record().auth_method).to.equal('other')
        expect(record().metadata).to.equal(undefined)
        expect(JSON.stringify(record())).to.not.include(authMethod)
        expect(consoleLine(record())).to.not.include(authMethod)
      }
    }
  })

  it('[guard] has a partial outcome for a passed step of a multi-step decision', () => {
    audit('auth.sign_in', { actor: USER, org: ORG, outcome: 'partial', authMethod: 'password' })
    expect(record()).to.include({ outcome: 'partial' })
  })

  describe('refusal tags', () => {
    it('are invisible: not in JSON, keys, a spread or the logged error, and the same error is returned', () => {
      const error = new Error('The email or password is incorrect.')
      expect(refusal(error, 'bad_password')).to.equal(error)
      expect(refusalReason(error)).to.equal('bad_password')
      expect(JSON.stringify(Object.assign({}, error))).to.equal('{}')
      expect(Object.keys(error)).to.deep.equal([])
      expect(Reflect.ownKeys(Object.assign({}, error))).to.deep.equal([])
      Logger.getInstance().warn('x', { error })
      expect(JSON.stringify(record())).to.not.include('bad_password')
    })

    it('keep the first reason, and are not read from a cause', () => {
      const error = refusal(refusal(new Error('x'), 'locked'), 'bad_password')
      expect(refusalReason(error)).to.equal('locked')
      const wrapped = new Error('db down', { cause: error })
      expect(refusalReason(wrapped)).to.equal(undefined)
      expect(refusalReason('locked')).to.equal(undefined)
      expect(refusalReason(undefined)).to.equal(undefined)
    })

    it('auditRefusal writes one failure line for an allowed tag and nothing for an untagged or foreign one', () => {
      auditRefusal('auth.sign_in', refusal(new Error('x'), 'locked'), { actor: USER, org: ORG, authMethod: 'otp' }, ['locked'])
      expect(log.callCount).to.equal(1)
      expect(record()).to.include({ message: 'auth.sign_in', outcome: 'failure', reason: 'locked', actor: USER, org_id: ORG, auth_method: 'otp' })
      auditRefusal('auth.sign_in', new Error('db down'), {}, ['locked'])
      auditRefusal('auth.sign_in', refusal(new Error('x'), 'not_admin'), {}, ['locked'])
      expect(log.callCount).to.equal(1)
    })
  })
})
