import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { SLACK_LINK_REQUIRED_MESSAGE } from '../../../../src/modules/enterprise_search/utils/scoped-request'
import { CONVERSATION_ROUTES, ConversationRoute, urlOf } from '../helpers/conversation-routes'
import { ACTORS, ORG, NOT_FOUND, Env, assertDecision, buildRouters, nothingHappened, seed } from '../helpers/conversation-world'
import { invokeRoute } from '../helpers/route-invoker'
import { linkSlackAccounts, slackCallerRequest, unlinkSlackAccounts, userRow } from '../helpers/slack-callers'
import { getUserRepository } from '../../../../src/libs/repositories/user'

/**
 * The internal routes authenticate the Slack bot's scoped token, hydrate it to the account its Slack person linked, and
 * only then reach the guard. The guard is the real one; a request it passes is answered 299 before the handler.
 */

const INTERNAL = CONVERSATION_ROUTES.filter((r) => r.internal)

/** The Slack people of these cases, each linked to the actor of the same name; `U0NOBODY` linked no account. */
const SLACK_ACCOUNTS = { U0A: String(ACTORS.A.userId), U0D: String(ACTORS.D.userId) }
const scoped = (slackUserId: string): Record<string, unknown> => slackCallerRequest(slackUserId, String(ORG))
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
    unlinkSlackAccounts()
  })

  const send = (r: ConversationRoute, s: ReturnType<typeof seed>, slackUserId: string) =>
    invokeRoute(env[r.kind], {
      method: r.method,
      url: urlOf(r, { conversationId: s.ids[r.kind], messageId: s.messageIds[r.kind] }),
      body: r.body,
      request: scoped(slackUserId),
    })

  it('covers the three internal send routes', () => {
    expect(INTERNAL.map((r) => r.id)).to.deep.equal(['C2', 'C4', 'A3'])
  })

  for (const r of INTERNAL) {
    it(`${r.method.toUpperCase()} ${r.path}: the owner’s scoped token reaches the guard as that user`, async () => {
      const s = seed()
      soleOrg()
      await linkSlackAccounts(String(ORG), SLACK_ACCOUNTS)
      sinon.stub(Users, 'findOne').callsFake((() => userRow(dbUser('A'))) as never)
      // One hydration reads the account once (its live row; then its standing).
      const find = sinon.spy(getUserRepository(), 'getLive')
      const out = await send(r, s, 'U0A')
      expect(find.calledOnce, 'user looked up once').to.equal(true)
      assertDecision(out, 'allow', r.id)
    })

    it(`${r.method.toUpperCase()} ${r.path}: a same-org user with no access gets a JSON 404 and no turn`, async () => {
      const s = seed()
      soleOrg()
      await linkSlackAccounts(String(ORG), SLACK_ACCOUNTS)
      sinon.stub(Users, 'findOne').callsFake((() => userRow(dbUser('D'))) as never)
      const out = await send(r, s, 'U0D')
      assertDecision(out, NOT_FOUND, r.id)
      nothingHappened(s)
    })
  }

  describe('a Slack caller with no account on the internal agent stream', () => {
    const a3 = INTERNAL.find((r) => r.id === 'A3')!

    it('is refused before the guard: no stand-in caller is made, and nothing runs', async () => {
      const s = seed()
      soleOrg()
      await linkSlackAccounts(String(ORG), SLACK_ACCOUNTS)
      sinon.stub(Users, 'findOne').callsFake((() => userRow(null)) as never)
      const execute = sinon.stub(AIServiceCommand.prototype, 'execute')
      const out = await send(a3, s, 'U0NOBODY')
      // Answered as the account the person linked, or not at all: they are told to link one (ADR-SLACKID-01).
      expect(out.error?.statusCode).to.equal(401)
      expect(out.error?.message).to.equal(SLACK_LINK_REQUIRED_MESSAGE)
      expect(execute.called, 'the agent service is never asked').to.equal(false)
      nothingHappened(s)
    })
  })
})
