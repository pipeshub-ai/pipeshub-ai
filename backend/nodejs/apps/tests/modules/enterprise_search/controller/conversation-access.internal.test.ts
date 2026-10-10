import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import * as cm from '../../../../src/modules/configuration_manager/controller/cm_controller'
import { stableObjectIdHexForExternalEmail } from '../../../../src/modules/enterprise_search/utils/scoped-request'
import { CONVERSATION_ROUTES, ConversationRoute, urlOf } from '../helpers/conversation-routes'
import { ACTORS, ORG, NOT_FOUND, Env, assertDecision, buildRouters, nothingHappened, seed } from '../helpers/conversation-world'
import { invokeRoute } from '../helpers/route-invoker'

/**
 * The internal routes (C2, C4, A3) authenticate a scoped service token, hydrate it to a user, and only then reach the guard.
 * The guard is the real one; a request it passes is answered 299 before the handler.
 */

const INTERNAL = CONVERSATION_ROUTES.filter((r) => r.internal)
const AGENT_ID = 'agent-1'

const scoped = (email: string): Record<string, unknown> => ({ tokenPayload: { email, orgId: String(ORG) } })
const dbUser = (who: keyof typeof ACTORS, extra: Record<string, unknown> = {}) => ({
  _id: ACTORS[who].userId,
  orgId: ORG,
  email: `${who}@example.com`,
  fullName: who,
  slug: who,
  ...extra,
})

describe('internal conversation routes: scoped token, then the guard', () => {
  let env: Env
  before(() => {
    env = buildRouters({ collab: false, keyValueStore: {} })
  })
  afterEach(() => {
    sinon.restore()
    env.teamCalls.count = 0
  })

  const send = (r: ConversationRoute, s: ReturnType<typeof seed>, email: string) =>
    invokeRoute(env[r.kind], {
      method: r.method,
      url: urlOf(r, { conversationId: s.ids[r.kind], messageId: s.messageIds[r.kind] }),
      body: r.body,
      request: scoped(email),
    })

  it('covers C2, C4 and A3', () => {
    expect(INTERNAL.map((r) => r.id)).to.deep.equal(['C2', 'C4', 'A3'])
  })

  for (const r of INTERNAL) {
    it(`SEC-18 ${r.id}: a scoped token for a disabled user is rejected before any session is read`, async () => {
      const s = seed()
      sinon.stub(Users, 'findOne').resolves(dbUser('A', { isDisabled: true }) as never)
      const reads = ChatSession.findOne as unknown as sinon.SinonStub
      reads.resetHistory()
      const out = await send(r, s, 'A@example.com')
      expect(out.error?.statusCode).to.equal(401)
      expect(reads.called, 'session read').to.equal(false)
      nothingHappened(s)
    })

    it(`PH04-09 ${r.id}: the owner’s scoped token reaches the guard as that user`, async () => {
      const s = seed()
      const find = sinon.stub(Users, 'findOne').resolves(dbUser('A') as never)
      const out = await send(r, s, 'A@example.com')
      expect(find.calledOnce, 'user looked up once').to.equal(true)
      assertDecision(out, 'allow', r.id)
    })

    it(`PH04-09 ${r.id}: a same-org user with no access gets a JSON 404 and no turn`, async () => {
      const s = seed()
      sinon.stub(Users, 'findOne').resolves(dbUser('D') as never)
      const out = await send(r, s, 'D@example.com')
      assertDecision(out, NOT_FOUND, r.id)
      nothingHappened(s)
    })
  }

  describe('PH04-10: a Slack service-account caller on A3', () => {
    const a3 = INTERNAL.find((r) => r.id === 'A3')!
    const BOT = 'bot@slack.example.com'

    const withServiceAccounts = (): void => {
      sinon.stub(Users, 'findOne').resolves(null as never)
      sinon.stub(cm, 'getSlackBotStore').resolves({ configs: [{ agentId: AGENT_ID }] } as never)
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { isServiceAccount: true } } as never)
      sinon.stub(Org, 'findOne').resolves({ _id: ORG } as never)
    }

    it('owns the session through its synthetic id: 200 with no team lookup; another synthetic id gets 404', async () => {
      const botId = new Types.ObjectId(stableObjectIdHexForExternalEmail(BOT))
      const s = seed({ userId: botId, initiator: botId, isShared: false, sharedWith: [], projectId: undefined, projectVisibility: undefined })
      withServiceAccounts()
      assertDecision(await send(a3, s, BOT), 'allow', 'A3 bot')
      assertDecision(await send(a3, s, 'other-bot@slack.example.com'), NOT_FOUND, 'A3 other bot')
      expect(env.teamCalls.count, 'team directory calls').to.equal(0)
    })
  })
})
