import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import mongoose from 'mongoose'
import {
  STAND_IN_TOKEN_TTL_SECONDS,
  hydrateScopedRequestAsUser,
  stableObjectIdHexForExternalEmail,
} from '../../../../src/modules/enterprise_search/utils/scoped-request'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { ForbiddenError, InternalServerError, NotFoundError } from '../../../../src/libs/errors/http.errors'
import { getUserRepository } from '../../../../src/libs/repositories/user'
import { getActivityRepository } from '../../../../src/libs/repositories/activity'
import { getCredentialRepository } from '../../../../src/libs/repositories/credential'
import * as cmController from '../../../../src/modules/configuration_manager/controller/cm_controller'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'

describe('hydrateScopedRequestAsUser', () => {
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

describe('hydrateScopedRequestAsUser: Slack caller without an account', () => {
  const appConfig = {
    jwtSecret: 'session-secret',
    scopedJwtSecret: 'scoped-secret',
    aiBackend: 'http://ai.local',
  } as any
  const email = 'outsider@example.com'
  let orgId: mongoose.Types.ObjectId
  let checkHeaders: Record<string, string> | undefined

  const makeReq = (): any => ({
    headers: { authorization: 'Bearer incoming-slack-token', cookie: 'session=abc', 'x-other': '1' },
    params: { agentKey: 'agent-1' },
    tokenPayload: { email, scopes: ['conversation:create'], configId: 'cfg-1' },
  })

  const stubCheck = (result: { statusCode: number; data?: unknown } | Error) => {
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: any) {
      checkHeaders = this.headers
      return result instanceof Error ? Promise.reject(result) : Promise.resolve(result as any)
    })
  }

  beforeEach(() => {
    checkHeaders = undefined
    orgId = new mongoose.Types.ObjectId()
    sinon.stub(Org, 'find').returns({
      limit: sinon.stub().returnsThis(),
      lean: sinon.stub().returnsThis(),
      exec: sinon.stub().resolves([{ _id: orgId }]),
    } as any)
    sinon.stub(Org, 'findOne').resolves({ _id: orgId } as any)
    sinon.stub(Users, 'findOne').resolves(null)
    sinon.stub(cmController, 'getSlackBotStore').resolves({
      configs: [{ id: 'cfg-1', agentId: 'agent-1' }],
    } as any)
  })

  afterEach(() => {
    sinon.restore()
  })

  it('asks the AI service with a short token naming the org and the stand-in user, and nothing else', async () => {
    stubCheck({ statusCode: 200, data: { isServiceAccount: true } })
    const req = makeReq()

    await hydrateScopedRequestAsUser(req, appConfig, {} as any)

    expect(Object.keys(checkHeaders!).sort()).to.deep.equal(['authorization', 'content-type'])
    const claims = jwt.verify(checkHeaders!.authorization.replace('Bearer ', ''), 'scoped-secret') as Record<string, any>
    expect(claims.userId).to.equal(stableObjectIdHexForExternalEmail(email))
    expect(String(claims.orgId)).to.equal(String(orgId))
    expect(claims.email).to.equal(email)
    expect(claims.scopes).to.deep.equal(['conversation:create'])
    expect(claims.isServiceAccount).to.equal(undefined)
    expect(claims.exp - claims.iat).to.equal(STAND_IN_TOKEN_TTL_SECONDS)
    expect(req.user.isServiceAccount).to.equal(true)
    expect(String(req.user.userId)).to.equal(stableObjectIdHexForExternalEmail(email))
  })

  for (const statusCode of [403, 404]) {
    it(`treats ${statusCode} from the check as "not a service agent"`, async () => {
      stubCheck({ statusCode, data: { detail: 'no' } })
      let error: any
      try {
        await hydrateScopedRequestAsUser(makeReq(), appConfig, {} as any)
      } catch (e) {
        error = e
      }
      expect(error).to.be.instanceOf(NotFoundError)
      expect(error.message).to.contain('User not found')
    })
  }

  for (const [label, result] of [
    ['a 401', { statusCode: 401, data: { detail: 'Token missing orgId' } }],
    ['a 500', { statusCode: 500, data: {} }],
    ['a network error', new Error('ECONNREFUSED')],
  ] as const) {
    it(`reports ${label} from the check as a server error, not a missing account`, async () => {
      stubCheck(result as any)
      let error: any
      try {
        await hydrateScopedRequestAsUser(makeReq(), appConfig, {} as any)
      } catch (e) {
        error = e
      }
      expect(error).to.be.instanceOf(InternalServerError)
      expect(error.message).to.not.contain('User not found')
    })
  }
})

describe('hydrateScopedRequestAsUser: Slack-only accounts', () => {
  const appConfig = {
    jwtSecret: 'session-secret',
    scopedJwtSecret: 'scoped-secret',
    aiBackend: 'http://ai.local',
  } as any
  const email = 'sam@acme.test'
  const fullMember = { slackFullMember: true, slackUserId: 'U1', slackTeamId: 'T1', slackFullName: 'Sam' }
  let orgId: mongoose.Types.ObjectId
  let findUser: sinon.SinonStub
  let createUser: sinon.SinonStub
  let inOrg: sinon.SinonStub
  let botConfig: Record<string, unknown>
  let check: { statusCode: number; data?: unknown }

  const req = (claims: Record<string, unknown> = fullMember, configId = 'cfg-1'): any => ({
    headers: { authorization: 'Bearer incoming-slack-token' },
    params: { agentKey: 'agent-1' },
    tokenPayload: { email, scopes: ['conversation:create'], configId, ...claims },
  })

  const run = async (r: any): Promise<any> => {
    try {
      await hydrateScopedRequestAsUser(r, appConfig, {} as any)
      return undefined
    } catch (e) {
      return e
    }
  }

  beforeEach(() => {
    orgId = new mongoose.Types.ObjectId()
    botConfig = { id: 'cfg-1', agentId: 'agent-1', createAccountsForSlackUsers: true }
    check = { statusCode: 200, data: { isServiceAccount: true } }
    sinon.stub(Org, 'find').returns({
      limit: sinon.stub().returnsThis(),
      lean: sinon.stub().returnsThis(),
      exec: sinon.stub().resolves([{ _id: orgId }]),
    } as any)
    sinon.stub(Org, 'findOne').resolves({ _id: orgId } as any)
    findUser = sinon.stub(Users, 'findOne').resolves(null)
    createUser = sinon
      .stub(getUserRepository(), 'create')
      .callsFake(async (org: string, doc: any) => ({ ...doc, orgId: org, _id: new mongoose.Types.ObjectId().toHexString() }))
    inOrg = sinon.stub(getUserRepository(), 'listByEmails').resolves([])
    sinon.stub(getActivityRepository(), 'record').resolves()
    sinon.stub(getCredentialRepository(), 'blockedUserIds').resolves([])
    sinon.stub(cmController, 'getSlackBotStore').callsFake(async () => ({ configs: [botConfig] }) as any)
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async () => check as any)
  })
  afterEach(() => sinon.restore())

  it('creates the account for a full member when the bot allows it, and hands out a service-agent token, never a session', async () => {
    const r = req()
    expect(await run(r)).to.equal(undefined)

    expect(createUser.calledOnce).to.equal(true)
    const created = await createUser.firstCall.returnValue
    expect(String(r.user.userId)).to.equal(String(created._id))
    expect(r.user.isServiceAccount).to.equal(true)
    const token = r.headers.authorization.replace('Bearer ', '')
    expect(() => jwt.verify(token, 'session-secret')).to.throw()
    expect(jwt.verify(token, 'scoped-secret')).to.include({ isServiceAccount: true, userId: String(created._id) })
  })

  it('keeps the stand-in behaviour when the setting is off', async () => {
    botConfig.createAccountsForSlackUsers = false
    const r = req()
    expect(await run(r)).to.equal(undefined)
    expect(createUser.called).to.equal(false)
    expect(String(r.user.userId)).to.equal(stableObjectIdHexForExternalEmail(email))
  })

  it('creates nothing for someone the bot did not vouch for (guest, other workspace, unconfirmed email)', async () => {
    const r = req({})
    expect(await run(r)).to.equal(undefined)
    expect(createUser.called).to.equal(false)
    expect(String(r.user.userId)).to.equal(stableObjectIdHexForExternalEmail(email))
  })

  it("creates nothing from another bot's token", async () => {
    expect(await run(req(fullMember, 'cfg-other'))).to.equal(undefined)
    expect(createUser.called).to.equal(false)
  })

  it('creates nothing, and refuses, when the agent is not a service agent', async () => {
    check = { statusCode: 403 }
    const error = await run(req())
    expect(error).to.be.instanceOf(NotFoundError)
    expect(createUser.called).to.equal(false)
  })

  it('never revives a deleted account', async () => {
    inOrg.resolves([{ _id: new mongoose.Types.ObjectId().toHexString(), orgId: String(orgId), kind: 'slack', isDeleted: true }])
    const r = req()
    expect(await run(r)).to.be.instanceOf(ForbiddenError)
    expect(createUser.called).to.equal(false)
    expect(r.user).to.equal(undefined)
  })

  describe('an existing Slack-only account', () => {
    const account = () => ({ _id: new mongoose.Types.ObjectId(), orgId, email, kind: 'slack', isDeleted: false })

    it('chats as itself with the service agent', async () => {
      const existing = account()
      findUser.resolves(existing)
      const r = req()
      expect(await run(r)).to.equal(undefined)
      expect(String(r.user.userId)).to.equal(String(existing._id))
      expect(r.user.isServiceAccount).to.equal(true)
      expect(createUser.called).to.equal(false)
    })

    it('is told to ask for an invite when the bot now points at a normal agent', async () => {
      findUser.resolves(account())
      check = { statusCode: 403 }
      const error = await run(req())
      expect(error).to.be.instanceOf(ForbiddenError)
      expect(error.message).to.contain('Ask your admin to invite you')
    })

    it('is refused once disabled', async () => {
      findUser.resolves({ ...account(), isDisabled: true })
      expect(await run(req())).to.be.instanceOf(ForbiddenError)
    })

    it('still chats when the setting is turned off later', async () => {
      botConfig.createAccountsForSlackUsers = false
      const existing = account()
      findUser.resolves(existing)
      const r = req({})
      expect(await run(r)).to.equal(undefined)
      expect(String(r.user.userId)).to.equal(String(existing._id))
    })
  })
})
