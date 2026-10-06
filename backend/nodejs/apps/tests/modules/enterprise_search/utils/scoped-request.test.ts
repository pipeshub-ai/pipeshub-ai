import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
type ScopedRequestModule = typeof import('../../../../src/modules/enterprise_search/utils/scoped-request')
const MODULE_SUFFIX = 'enterprise_search/utils/scoped-request.ts'

// The module takes its logger once, at load, so load a private copy that logs
// into the recorder, then put the cached module back.
const privateModule = (recorder: Logger): ScopedRequestModule => {
  const cached = Object.keys(require.cache).filter((key) => key.endsWith(MODULE_SUFFIX))
  const originals = cached.map((key) => [key, require.cache[key]] as const)
  for (const key of cached) delete require.cache[key]
  const getInstance = sinon.stub(Logger, 'getInstance').returns(recorder)
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require('../../../../src/modules/enterprise_search/utils/scoped-request') as ScopedRequestModule
  } finally {
    getInstance.restore()
    for (const key of Object.keys(require.cache)) {
      if (key.endsWith(MODULE_SUFFIX)) delete require.cache[key]
    }
    for (const [key, mod] of originals) require.cache[key] = mod
  }
}

import jwt from 'jsonwebtoken'
import {
  STAND_IN_TOKEN_TTL_SECONDS,
  hydrateScopedRequestAsUser,
} from '../../../../src/modules/enterprise_search/utils/scoped-request'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { Logger } from '../../../../src/libs/services/logger.service'
import { UnauthorizedError } from '../../../../src/libs/errors/http.errors'
import * as cm from '../../../../src/modules/configuration_manager/controller/cm_controller'

const ORG = new mongoose.Types.ObjectId()
const OTHER_ORG = new mongoose.Types.ObjectId()
const appConfig: any = { jwtSecret: 's', scopedJwtSecret: 'sc', aiBackend: 'http://ai' }

const makeReq = (tokenPayload: any, params: any = {}): any => ({
  tokenPayload,
  params,
  headers: {},
})

const activeUser = (extra: any = {}) => ({
  _id: new mongoose.Types.ObjectId(),
  orgId: ORG,
  email: 'a@x.com',
  fullName: 'A',
  slug: 'a',
  ...extra,
})

describe('hydrateScopedRequestAsUser (S4b / F-9)', () => {
  let findOne: sinon.SinonStub
  let genToken: sinon.SinonStub
  let orgFind: sinon.SinonStub

  beforeEach(() => {
    findOne = sinon.stub(Users, 'findOne')
    genToken = sinon.stub(AuthTokenService.prototype, 'generateToken').returns('jwt')
    orgFind = sinon.stub(Org, 'find').returns({
      limit: sinon.stub().returnsThis(),
      lean: sinon.stub().returnsThis(),
      exec: sinon.stub().resolves([{ _id: ORG }]),
    } as any)
  })
  afterEach(() => sinon.restore())

  it('SEC-18: rejects a disabled user and mints no JWT', async () => {
    findOne.resolves(activeUser({ isDisabled: true }))
    const req = makeReq({ email: 'a@x.com', orgId: ORG.toString() })
    let err: unknown
    try { await hydrateScopedRequestAsUser(req, appConfig) } catch (e) { err = e }
    expect(err).to.be.instanceOf(UnauthorizedError)
    expect(genToken.called).to.be.false
    expect(req.user).to.be.undefined
  })

  it('SEC-18: rejects a service-kind user and mints no JWT', async () => {
    findOne.resolves(activeUser({ kind: 'service' }))
    const req = makeReq({ email: 'a@x.com' })
    let err: unknown
    try { await hydrateScopedRequestAsUser(req, appConfig) } catch (e) { err = e }
    expect(err).to.be.instanceOf(UnauthorizedError)
    expect(genToken.called).to.be.false
  })

  it('SEC-18: rejects a user from another org (token orgId differs) and filters by orgId', async () => {
    findOne.callsFake(async (q: any) => (q.orgId ? null : activeUser({ orgId: OTHER_ORG })))
    const warn = sinon.stub()
    const { hydrateScopedRequestAsUser } = privateModule({ warn } as unknown as Logger)
    const req = makeReq({ email: 'a@x.com', orgId: ORG.toString() })
    let err: unknown
    try { await hydrateScopedRequestAsUser(req, appConfig) } catch (e) { err = e }
    expect(err).to.be.instanceOf(UnauthorizedError)
    expect((err as Error).message).to.equal('Unauthorized')
    expect(findOne.firstCall.args[0]).to.deep.include({ email: 'a@x.com', isDeleted: false, orgId: ORG.toString() })
    expect(genToken.called).to.be.false
    expect(warn.calledOnce).to.be.true
    expect(warn.firstCall.args[1]).to.deep.equal({ reason: 'org_mismatch' })
    expect(JSON.stringify(warn.firstCall.args)).to.not.include('a@x.com')
  })

  it('SEC-18: hydrates an active user as before', async () => {
    const user = activeUser()
    findOne.resolves(user)
    const req = makeReq({ email: 'a@x.com', orgId: ORG.toString() })
    await hydrateScopedRequestAsUser(req, appConfig)
    expect(req.user.userId).to.equal(user._id)
    expect(req.headers.authorization).to.equal('Bearer jwt')
  })

  it('token without orgId looks the user up in the sole org', async () => {
    findOne.resolves(activeUser())
    const req = makeReq({ email: 'a@x.com' })
    await hydrateScopedRequestAsUser(req, appConfig)
    expect(String(findOne.firstCall.args[0].orgId)).to.equal(ORG.toString())
    expect(findOne.callCount).to.equal(1)
  })

  it('token that names its org never guesses the org', async () => {
    findOne.resolves(activeUser())
    const req = makeReq({ email: 'a@x.com', orgId: ORG.toString() })
    await hydrateScopedRequestAsUser(req, appConfig)
    expect(orgFind.called).to.be.false
    expect(findOne.firstCall.args[0].orgId).to.equal(ORG.toString())
  })

  it('PH01-11: a disabled user does not fall into the Slack service-account branch', async () => {
    findOne.resolves(activeUser({ isDisabled: true }))
    const slackStore = sinon.stub(cm, 'getSlackBotStore').resolves({ configs: [{ agentId: 'ag1' }] } as any)
    const req = makeReq({ email: 'a@x.com' }, { agentKey: 'ag1' })
    let err: unknown
    try { await hydrateScopedRequestAsUser(req, appConfig, {} as any) } catch (e) { err = e }
    expect(err).to.be.instanceOf(UnauthorizedError)
    expect(slackStore.called).to.be.false
  })

  it('PH01-11: an org mismatch never reaches the Slack service-account branch', async () => {
    findOne.callsFake(async (q: any) => (q.orgId ? null : activeUser({ orgId: OTHER_ORG })))
    const { hydrateScopedRequestAsUser } = privateModule({ warn: sinon.stub() } as unknown as Logger)
    const slackStore = sinon.stub(cm, 'getSlackBotStore').resolves({ configs: [{ agentId: 'ag1' }] } as any)
    const req = makeReq({ email: 'a@x.com', orgId: ORG.toString() }, { agentKey: 'ag1' })
    let err: unknown
    try { await hydrateScopedRequestAsUser(req, appConfig, {} as any) } catch (e) { err = e }
    expect(err).to.be.instanceOf(UnauthorizedError)
    expect((err as Error).message).to.equal('Unauthorized')
    expect(slackStore.called).to.be.false
  })

  it('PH01-11b: a token with an orgId and no user in any org still hydrates through the Slack service-account fallback', async () => {
    findOne.resolves(null)
    const slackStore = sinon.stub(cm, 'getSlackBotStore').resolves({ configs: [{ agentId: 'ag1' }] } as any)
    sinon.stub(Org, 'findOne').resolves({ _id: ORG } as any)
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { isServiceAccount: true } } as any)
    const req = makeReq({ email: 'ext@x.com', orgId: ORG.toString() }, { agentKey: 'ag1' })

    await hydrateScopedRequestAsUser(req, appConfig, {} as any)

    expect(findOne.callCount).to.equal(2)
    expect(slackStore.calledOnce).to.be.true
    expect(req.user.isServiceAccount).to.be.true
    expect(String(req.user.orgId)).to.equal(String(ORG))
    expect(req.user.email).to.equal('ext@x.com')
  })
})

describe('hydrateScopedRequestAsUser stand-in token', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('gives the token it mints the role claim Node requires of a session, as member', async () => {
    const orgId = new mongoose.Types.ObjectId()
    sinon.stub(Org, 'find').returns({
      limit: sinon.stub().returnsThis(),
      lean: sinon.stub().returnsThis(),
      exec: sinon.stub().resolves([{ _id: orgId }]),
    } as any)
    sinon.stub(Users, 'findOne').resolves({
      _id: new mongoose.Types.ObjectId(),
      orgId,
      email: 'person@example.com',
      fullName: 'Person',
      role: 'admin',
    } as any)
    const req: any = {
      headers: { authorization: 'Bearer slack-service-token' },
      params: {},
      tokenPayload: { email: 'person@example.com' },
    }

    await hydrateScopedRequestAsUser(req, {
      jwtSecret: 'session-secret',
      scopedJwtSecret: 'scoped-secret',
    } as any)

    const token = req.headers.authorization.replace('Bearer ', '')
    const claims = jwt.verify(token, 'session-secret') as Record<string, unknown>
    expect(claims.role).to.equal('member')
    expect(claims.email).to.equal('person@example.com')
    // Node honours it like a session, so it must not outlive the request.
    expect((claims.exp as number) - (claims.iat as number)).to.equal(STAND_IN_TOKEN_TTL_SECONDS)
    expect(STAND_IN_TOKEN_TTL_SECONDS).to.be.at.most(60)
  })
})
