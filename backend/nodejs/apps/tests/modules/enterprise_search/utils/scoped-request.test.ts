import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import mongoose from 'mongoose'
import * as scopedRequest from '../../../../src/modules/enterprise_search/utils/scoped-request'
import {
  SLACK_LINK_REQUIRED_MESSAGE,
  STAND_IN_TOKEN_TTL_SECONDS,
  hydrateScopedRequestAsUser,
} from '../../../../src/modules/enterprise_search/utils/scoped-request'
import * as cmController from '../../../../src/modules/configuration_manager/controller/cm_controller'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { setUserRepository } from '../../../../src/libs/repositories/user'
import { setSlackIdentityLinksForTests } from '../../../../src/modules/configuration_manager/slack-identity/slack-linked-account'
import { setSlackUserLinkRepository } from '../../../../src/libs/repositories/slack-user-link'
import { InMemorySlackUserLinks } from '../../../support/slack/in-memory-slack-links'

const ORG = new mongoose.Types.ObjectId().toHexString()
const PERSON = new mongoose.Types.ObjectId().toHexString()
const APP_CONFIG = { jwtSecret: 'session-secret', scopedJwtSecret: 'scoped-secret' } as any
const SLACK = { configId: 'cfg-1', slackTeamId: 'T0HOME', slackUserId: 'U0PERSON' }

/** The org's Slack links: `linked` is the account U0PERSON linked, or nobody. */
function links(linked: string | null) {
  return {
    find: async (orgId: string) => (linked && orgId === ORG ? { orgId: ORG, userId: linked } : null),
    revoke: async () => undefined,
  } as any
}

const people = {
  getLive: async (orgId: string, id: string) =>
    orgId === ORG && id === PERSON ? { _id: PERSON, orgId: ORG, email: 'person@example.com', fullName: 'Person' } : null,
  getAccountState: async () => ({ kind: 'human' as const }),
  getLiveByEmail: sinon.stub().rejects(new Error('a scoped token is never bound by its email')),
}

describe('hydrateScopedRequestAsUser', () => {
  beforeEach(() => {
    sinon.stub(scopedRequest, 'resolveSoleOrgId').resolves(ORG)
    setUserRepository(people as never)
  })
  afterEach(() => {
    sinon.restore()
    setSlackIdentityLinksForTests(undefined)
    setSlackUserLinkRepository()
    setUserRepository()
  })

  it('gives the token it mints the role claim Node requires of a session, as member', async () => {
    setSlackIdentityLinksForTests(links(PERSON))
    const req: any = { headers: { authorization: 'Bearer slack-service-token' }, params: {}, tokenPayload: { ...SLACK } }

    await hydrateScopedRequestAsUser(req, APP_CONFIG)

    const token = req.headers.authorization.replace('Bearer ', '')
    const claims = jwt.verify(token, 'session-secret') as Record<string, unknown>
    expect(claims.role).to.equal('member')
    expect(claims.email).to.equal('person@example.com')
    // Node honours it like a session, so it must not outlive the request.
    expect((claims.exp as number) - (claims.iat as number)).to.equal(STAND_IN_TOKEN_TTL_SECONDS)
    expect(STAND_IN_TOKEN_TTL_SECONDS).to.be.at.most(60)
  })

  it('refuses a link that names a service account and mints nothing', async () => {
    setSlackIdentityLinksForTests(links(PERSON))
    setUserRepository({ ...people, getAccountState: async () => ({ kind: 'service' as const }) } as never)
    const req: any = { headers: { authorization: 'Bearer slack-service-token' }, params: {}, tokenPayload: { ...SLACK } }

    const error = await hydrateScopedRequestAsUser(req, APP_CONFIG).then(() => null, (e: any) => e)

    expect(error?.message).to.equal(SLACK_LINK_REQUIRED_MESSAGE)
    expect(req.user).to.equal(undefined)
    expect(req.headers.authorization).to.equal('Bearer slack-service-token')
  })

  // An agent answers as the person asking, so without a linked account there is no one to answer as, and no stand-in
  // is made up, whatever kind of agent it is.
  it('refuses a person without an account on an agent set up for Slack, and mints nothing', async () => {
    setSlackUserLinkRepository(new InMemorySlackUserLinks())
    sinon.stub(cmController, 'getSlackBotStore').resolves({ configs: [{ agentId: 'agent-7' }] } as any)
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { isServiceAccount: true } } as any)
    const req: any = {
      headers: { authorization: 'Bearer slack-service-token' },
      params: { agentKey: 'agent-7' },
      tokenPayload: { ...SLACK, email: 'Guest@Partner.example' },
    }

    const error = await hydrateScopedRequestAsUser(req, APP_CONFIG, {} as any).then(() => null, (e: any) => e)

    expect(error?.statusCode).to.equal(401)
    expect(error?.message).to.equal(SLACK_LINK_REQUIRED_MESSAGE)
    expect(req.user).to.equal(undefined)
    expect(req.headers.authorization).to.equal('Bearer slack-service-token')
    expect(execute.called).to.equal(false)
  })

  // BOTTOKEN-1: the bot's tokens always name their configuration, so no route binds a person by a token's email.
  for (const [name, claims] of [
    ['an email alone', { email: 'person@example.com' }],
    ['an email and an org', { email: 'person@example.com', orgId: ORG }],
    ['an email and a Slack identity', { email: 'person@example.com', slackTeamId: 'T0HOME', slackUserId: 'U0PERSON' }],
  ] as const) {
    it(`refuses a scoped token that names no Slack bot configuration with 401 (${name}), even for a real person's email`, async () => {
      setSlackIdentityLinksForTests(links(PERSON))
      sinon.stub(cmController, 'getSlackBotStore').resolves({ configs: [{ agentId: 'agent-7' }] } as any)
      const execute = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { isServiceAccount: true } } as any)
      const req: any = { headers: { authorization: 'Bearer scoped' }, params: { agentKey: 'agent-7' }, tokenPayload: { ...claims } }

      const error = await hydrateScopedRequestAsUser(req, APP_CONFIG, {} as any).then(() => null, (e: any) => e)

      expect(error?.statusCode).to.equal(401)
      expect(String(error?.message)).to.contain('does not name its Slack bot configuration')
      expect(req.user).to.equal(undefined)
      expect(req.headers.authorization).to.equal('Bearer scoped')
      expect(people.getLiveByEmail.called).to.equal(false)
      expect(execute.called).to.equal(false)
    })
  }
})
