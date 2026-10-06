import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { CONVERSATION_ROUTES, ConversationRoute, urlOf } from '../helpers/conversation-routes'
import { ACTORS, ORG, buildRouters } from '../helpers/conversation-world'
import { invokeRoute } from '../helpers/route-invoker'
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { turnWorld } from '../helpers/turn-world'
import { settle } from './chat-test-harness'

/** C2, C4 and A3 reuse the handlers of C1, C3 and A2, so they run the same pipeline behind the scoped token. */
const INTERNAL = CONVERSATION_ROUTES.filter((r) => r.internal)
const PLAIN = new Set(['C2'])

describe('PH05-05: internal follow-up routes run the lease pipeline', () => {
  afterEach(() => sinon.restore())

  for (const r of INTERNAL) {
    it(`${r.id}: a scoped token for B (write) takes the lease, writes B’s rows, and releases`, async () => {
      const w = turnWorld()
      const env = buildRouters({ collab: true, stop: false, leases: w.leases as never, readiness: w.readiness as never, keyValueStore: {} })
      sinon.stub(Users, 'findOne').resolves({ _id: ACTORS.B.userId, orgId: ORG, email: 'B@example.com', fullName: 'B', slug: 'B' } as never)
      const plain = PLAIN.has(r.id)
      if (plain) w.s.ai.reply(r.kind === 'chat' ? /\/api\/v1\/chat$/ : /\/chat$/, 200, { answer: 'Internal answer', citations: [], confidence: 'High' })

      const pending = invokeRoute(env[r.kind as ConversationRoute['kind']], {
        method: r.method,
        url: urlOf(r, { conversationId: w.s.ids[r.kind], messageId: w.s.messageIds[r.kind] }),
        body: { ...r.body, clientMessageId: 'slack-1' },
        request: { tokenPayload: { email: 'B@example.com', orgId: String(ORG) } },
      })
      if (!plain) {
        await settle()
        expect(w.handles).to.have.length(1)
        expect(w.session(r.kind).activeRun.userId.toString()).to.equal(String(ACTORS.B.userId))
        await w.answerStream('Internal answer')
      }
      const out = await pending

      expect(out.error, r.id).to.equal(undefined)
      const [, , question, reply] = w.rows(r.kind)
      expect(String(question!.authorUserId)).to.equal(String(ACTORS.B.userId))
      expect(question!.clientMessageId).to.equal('slack-1')
      expect(String(reply!.requestedBy)).to.equal(String(ACTORS.B.userId))
      expect(reply!.content).to.equal('Internal answer')
      expect(w.session(r.kind).activeRun ?? null).to.equal(null)
      expect((w.handles[0]!.release as sinon.SinonSpy).callCount).to.equal(1)
    })
  }

  describe('first sends: the internal create routes', () => {
    const FIRST_SENDS = [
      { id: 'internal/create', kind: 'chat' as const, url: '/internal/create', plain: true },
      { id: 'internal/stream', kind: 'chat' as const, url: '/internal/stream', plain: false },
      { id: 'agent internal/stream', kind: 'agent' as const, url: '/agent-1/conversations/internal/stream', plain: false },
    ]

    for (const r of FIRST_SENDS) {
      it(`PH05-06: ${r.id} takes the lease for the token’s user, keys the session, and rejects a repeat with a 409 before any byte`, async () => {
        const w = turnWorld()
        const env = buildRouters({ collab: true, stop: false, leases: w.leases as never, readiness: w.readiness as never, keyValueStore: {}, deps: w.deps })
        sinon.stub(Users, 'findOne').resolves({ _id: ACTORS.B.userId, orgId: ORG, email: 'B@example.com', fullName: 'B', slug: 'B' } as never)
        if (r.plain) w.s.ai.reply(/\/api\/v1\/chat$/, 200, { answer: 'Internal answer', citations: [], confidence: 'High' })
        const send = (): ReturnType<typeof invokeRoute> =>
          invokeRoute(env[r.kind], {
            method: 'post',
            url: r.url,
            body: { query: 'Hello from Slack', chatMode: 'quick', clientMessageId: 'slack-1' },
            request: { tokenPayload: { email: 'B@example.com', orgId: String(ORG) } },
          })

        const pending = send()
        await settle()
        if (!r.plain) {
          expect(w.created()[0]!.activeRun.userId.toString()).to.equal(String(ACTORS.B.userId))
          await w.answerStream('Internal answer')
        }
        const out = await pending

        expect(out.error, r.id).to.equal(undefined)
        const [fresh] = w.created()
        expect(fresh).to.include({ creationKey: 'slack-1', status: 'Complete' })
        expect(String(fresh!.initiator)).to.equal(String(ACTORS.B.userId))
        const [question, reply] = w.rowsOf(fresh!._id)
        expect(String(question!.authorUserId)).to.equal(String(ACTORS.B.userId))
        expect(question!.clientMessageId).to.equal('slack-1')
        expect(String(reply!.requestedBy)).to.equal(String(ACTORS.B.userId))
        expect(reply!.content).to.equal('Internal answer')
        expect(fresh!.activeRun ?? null).to.equal(null)
        expect((w.handles[0]!.release as sinon.SinonSpy).callCount).to.equal(1)

        const repeat = await send()
        expect(repeat.error?.code, r.id).to.equal(COLLAB_ERROR_CODES.DUPLICATE_MESSAGE)
        expect(repeat.status).to.equal(409)
        expect((repeat.body as { error: { code: string; details: Record<string, unknown> } }).error).to.deep.include({ code: COLLAB_ERROR_CODES.DUPLICATE_MESSAGE })
        expect((repeat.body as { error: { details: Record<string, unknown> } }).error.details).to.include({ conversationId: String(fresh!._id), messageId: String(question!._id) })
        expect(repeat.res.headersSent && repeat.res.body !== '', 'no SSE bytes').to.equal(false)
        expect(w.created()).to.have.length(1)
        expect(w.handles).to.have.length(1)
      })
    }
  })
})
