import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import mongoose from 'mongoose'
import * as scopedRequest from '../../../../src/modules/enterprise_search/utils/scoped-request'
import {
  STAND_IN_TOKEN_TTL_SECONDS,
  hydrateScopedRequestAsUser,
  stableObjectIdHexForExternalEmail,
} from '../../../../src/modules/enterprise_search/utils/scoped-request'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
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

  it('QA15G-10: a person without an account, answered by a service-account agent, gets a token naming them and the agent', async () => {
    // With the configuration store at hand the links are read from the org's link rows: none here.
    setSlackUserLinkRepository(new InMemorySlackUserLinks())
    sinon.stub(Org, 'findOne').resolves({ _id: ORG } as any)
    sinon.stub(cmController, 'getSlackBotStore').resolves({ configs: [{ agentId: 'agent-7' }] } as any)
    // The query service's answer to "is this agent a service account".
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { isServiceAccount: true } } as any)
    const req: any = {
      headers: { authorization: 'Bearer slack-service-token' },
      params: { agentKey: 'agent-7' },
      tokenPayload: { ...SLACK, email: 'Guest@Partner.example' },
    }

    await hydrateScopedRequestAsUser(req, APP_CONFIG, {} as any)

    const claims = jwt.verify(req.headers.authorization.replace('Bearer ', ''), 'scoped-secret') as Record<string, unknown>
    expect(claims).to.include({
      userId: stableObjectIdHexForExternalEmail('slack-t0home-u0person@slack.invalid'),
      isServiceAccount: true,
      agentKey: 'agent-7',
    })
    expect(String(claims.orgId)).to.equal(ORG)
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
