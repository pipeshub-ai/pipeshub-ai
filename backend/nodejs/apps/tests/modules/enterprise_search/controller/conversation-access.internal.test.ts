import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { UNIDENTIFIED_CALLER_MESSAGE } from '../../../../src/modules/enterprise_search/utils/scoped-request'
import { CONVERSATION_ROUTES, ConversationRoute, urlOf } from '../helpers/conversation-routes'
import { ACTORS, ORG, NOT_FOUND, Env, assertDecision, buildRouters, nothingHappened, seed } from '../helpers/conversation-world'
import { invokeRoute } from '../helpers/route-invoker'

/**
 * The internal routes authenticate a scoped service token, hydrate it to a user, and only then reach the guard.
 * The guard is the real one; a request it passes is answered 299 before the handler.
 */

const INTERNAL = CONVERSATION_ROUTES.filter((r) => r.internal)
const AGENT_ID = 'agent-1'

const scoped = (email: string): Record<string, unknown> => ({ tokenPayload: { email, orgId: String(ORG) } })
/** Hydration resolves the deployment's only org before it looks up the user. Stub after `seed()`, which restores sinon. */
const soleOrg = (): void => {
  sinon.stub(Org, 'find').returns({ limit: sinon.stub().returnsThis(), lean: sinon.stub().returnsThis(), exec: sinon.stub().resolves([{ _id: ORG }]) } as never)
}
const dbUser = (who: keyof typeof ACTORS) => ({
  _id: ACTORS[who].userId,
  orgId: ORG,
  email: `${who}@example.com`,
  fullName: who,
  slug: who,
})

describe('internal conversation routes: scoped token, then the guard', () => {
  let env: Env
  before(() => {
    env = buildRouters({ keyValueStore: {} })
  })
  afterEach(() => {
    sinon.restore()
  })

  const send = (r: ConversationRoute, s: ReturnType<typeof seed>, email: string) =>
    invokeRoute(env[r.kind], {
      method: r.method,
      url: urlOf(r, { conversationId: s.ids[r.kind], messageId: s.messageIds[r.kind] }),
      body: r.body,
      request: scoped(email),
    })

  it('covers the three internal send routes', () => {
    expect(INTERNAL.map((r) => r.id)).to.deep.equal(['C2', 'C4', 'A3'])
  })

  for (const r of INTERNAL) {
    it(`${r.method.toUpperCase()} ${r.path}: the owner’s scoped token reaches the guard as that user`, async () => {
      const s = seed()
      soleOrg()
      const find = sinon.stub(Users, 'findOne').resolves(dbUser('A') as never)
      const out = await send(r, s, 'A@example.com')
      expect(find.calledOnce, 'user looked up once').to.equal(true)
      assertDecision(out, 'allow', r.id)
    })

    it(`${r.method.toUpperCase()} ${r.path}: a same-org user with no access gets a JSON 404 and no turn`, async () => {
      const s = seed()
      soleOrg()
      sinon.stub(Users, 'findOne').resolves(dbUser('D') as never)
      const out = await send(r, s, 'D@example.com')
      assertDecision(out, NOT_FOUND, r.id)
      nothingHappened(s)
    })
  }

  describe('a Slack caller with no account on the internal agent stream', () => {
    const a3 = INTERNAL.find((r) => r.id === 'A3')!

    it('is refused before the guard: no stand-in caller is made, and nothing runs', async () => {
      const s = seed()
      soleOrg()
      sinon.stub(Users, 'findOne').resolves(null as never)
      const execute = sinon.stub(AIServiceCommand.prototype, 'execute')
      const out = await send(a3, s, 'bot@slack.example.com')
      expect(out.error?.statusCode).to.equal(403)
      expect(out.error?.message).to.equal(UNIDENTIFIED_CALLER_MESSAGE)
      expect(execute.called, 'the agent service is never asked').to.equal(false)
      nothingHappened(s)
    })
  })
})
