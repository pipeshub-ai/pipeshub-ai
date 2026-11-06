import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import winston from 'winston'
import { audit } from '../../../src/libs/services/audit'
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
})
