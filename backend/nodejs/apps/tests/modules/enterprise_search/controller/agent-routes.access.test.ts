import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CONVERSATION_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/conversation/domain/errors'
import { ConversationOperation } from '../../../../src/modules/enterprise_search/services/conversation/domain/types'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import * as conversationArtifacts from '../../../../src/modules/artifacts/services/conversation-artifacts.service'
import { settle } from './chat-test-harness'
import { finalAnswer } from './streaming-flows'
import { invokeRoute, RouteOutcome } from '../helpers/route-invoker'
import { AGENT_KEY as KEY, CONVERSATION_ROUTES, ConversationRoute, urlOf } from '../helpers/conversation-routes'
import { ACTORS as WORLD, ActorName as WorldActor, Env, NOT_FOUND as WORLD_NOT_FOUND, ORG, Seed as World, buildRouters, seed } from '../helpers/conversation-world'

// The agent-kind rows of the shared actor world: A owner, B write, C read, P project viewer, D stranger, E other org.
const ACTOR_OF = { owner: 'A', write: 'B', read: 'C', projectViewer: 'P', stranger: 'D', crossOrg: 'E' } as const
type ActorName = keyof typeof ACTOR_OF
const ACTORS = Object.fromEntries(Object.entries(ACTOR_OF).map(([name, world]) => [name, WORLD[world as WorldActor]])) as Record<ActorName, (typeof WORLD)[WorldActor]>

interface RouteCase extends ConversationRoute {
  /** Seed overrides for the actor. */
  seedFor?: (actor: ActorName) => Record<string, unknown>
  ok?: number
  /** What a permitted caller must leave behind; runs only for 'allow'. */
  effect?: (w: World, out: Outcome) => void
}

function expectTurnSaved(w: World): void {
  expect(w.ai.calls.filter((c) => c.url.endsWith(`/api/v1/agent/${KEY}/chat`)), 'non-streaming AI calls').to.have.length(1)
  const messages = w.store.messagesOf(w.ids.agent)
  expect(messages, 'user query and answer appended').to.have.length(4)
  expect(messages.at(-1)?.messageType).to.equal('bot_response')
}

const extras: Record<string, Partial<RouteCase>> = {
  A1: { ok: 200, effect: expectTurnSaved },
  A5: { ok: 200 },
  A6: { ok: 200 },
  A7: { ok: 200 },
  A8: { ok: 200 },
  A9: { ok: 200 },
  A10: { ok: 200, effect: (w) => {
    const stored = w.store.session(w.ids.agent)
    expect(stored?.projectId, 'projectId unset').to.equal(undefined)
    expect(stored?.projectVisibility, 'projectVisibility unset').to.equal(undefined)
  } },
  A11: { ok: 200, effect: (w) => {
    expect(w.store.session(w.ids.agent)?.projectVisibility).to.equal('private')
  } },
  A12: { ok: 200 },
  A13: { ok: 200, seedFor: () => ({ isArchived: true }) },
}

const ROUTES: RouteCase[] = CONVERSATION_ROUTES.filter((r) => r.kind === 'agent').map((r) => ({ ...r, ...extras[r.id] }))

type Outcome = RouteOutcome

let off: Env

function seedWorld(over: Record<string, unknown> = {}) {
  const w = seed(over)
  w.ai.reply(/\/api\/v1\/agent\/[^/]+\/chat$/, 200, { answer: 'Here is more detail.', citations: [], confidence: 'High' })
  // Deleting a chat first deletes its artifacts through the connectors service.
  sinon.stub(conversationArtifacts, 'deleteConversationArtifacts').resolves()
  return w
}

function invoke(route: RouteCase, url: string, req: Record<string, unknown>): Promise<Outcome> {
  const { user, ...request } = req
  return invokeRoute(off.agent, {
    method: route.method,
    url,
    body: route.body,
    user: user as Record<string, unknown> | undefined,
    request,
  })
}

async function call(route: RouteCase, w: World, actor: ActorName, over: { agentKey?: string; kind?: 'chat' | 'agent' } = {}): Promise<Outcome> {
  const url = urlOf(route, { conversationId: w.ids[over.kind ?? 'agent'], messageId: w.messageIds[over.kind ?? 'agent'], agentKey: over.agentKey ?? KEY })
  const p = invoke(route, url, { user: { ...ACTORS[actor], email: `${actor}@example.com` } })
  await settle()
  w.ai.send('RUN_FINISHED', finalAnswer('ok'))
  w.ai.finish()
  return p
}

const GUARD_CODES: string[] = Object.values(CONVERSATION_ERROR_CODES)
const guardCode = (o: Outcome): string | undefined =>
  o.error?.code !== undefined && GUARD_CODES.includes(o.error.code) ? o.error.code : undefined

const nothingHappened = (w: World): void => {
  expect(w.store.writes, 'writes').to.deep.equal([])
  expect(w.ai.calls, 'AI service calls').to.deep.equal([])
}

type Expectation = 'allow' | [number, string]
const NOT_FOUND: Expectation = [WORLD_NOT_FOUND === 'allow' ? 404 : WORLD_NOT_FOUND.status, CONVERSATION_ERROR_CODES.NOT_FOUND]

function expectedFlagOff(actor: ActorName, op: ConversationOperation): Expectation {
  if (actor === 'owner') return 'allow'
  if (actor === 'projectViewer' && op === 'read') return 'allow'
  // A share row on an agent chat opens it read-only (a legacy write row is read).
  if ((actor === 'read' || actor === 'write') && op === 'read') return 'allow'
  return NOT_FOUND
}

function assertOutcome(out: Outcome, expected: Expectation, label: string, ok?: number, effect?: () => void): void {
  if (expected === 'allow') {
    expect(guardCode(out), `${label}: guard let the request through`).to.equal(undefined)
    if (ok !== undefined) {
      expect(out.error, `${label}: handler succeeded`).to.equal(undefined)
      expect(out.status, `${label}: handler status`).to.equal(ok)
      effect?.()
    }
    return
  }
  expect(out.error?.statusCode, `${label} status`).to.equal(expected[0])
  expect(out.error?.code, `${label} code`).to.equal(expected[1])
  expect(out.res.headersSent, `${label}: no SSE header before the guard`).to.equal(false)
}

describe('agent conversation routes: guard decisions through the router', () => {
  before(() => {
    off = buildRouters({ stop: false })
  })
  afterEach(() => {
    sinon.restore()
  })

  // Guard mounting per route is asserted in routes/es.routes.guards.test.ts.

  describe('the default agent rules (read = owner, recipients, project; everything else = owner; every denial 404)', () => {
    for (const route of ROUTES) {
      for (const actor of Object.keys(ACTORS) as ActorName[]) {
        it(`${route.method.toUpperCase()} ${route.path} ${route.op}: ${actor}`, async () => {
          const w = seedWorld(route.seedFor?.(actor))
          const expected = expectedFlagOff(actor, route.op)
          const out = await call(route, w, actor)
          assertOutcome(out, expected, `${route.id} ${actor}`, route.ok, () => route.effect?.(w, out))
          if (expected !== 'allow') nothingHappened(w)
        })
      }

      it(`${route.method.toUpperCase()} ${route.path}: a deleted conversation, the wrong agent key and a chat id are all 404 for the owner`, async () => {
        const deleted = seedWorld({ ...route.seedFor?.('owner'), isDeleted: true })
        assertOutcome(await call(route, deleted, 'owner'), NOT_FOUND, `${route.id} deleted`)
        nothingHappened(deleted)
        sinon.restore()

        const w = seedWorld(route.seedFor?.('owner'))
        assertOutcome(await call(route, w, 'owner', { agentKey: 'agent-2' }), NOT_FOUND, `${route.id} wrong agentKey`)
        nothingHappened(w)
        sinon.restore()

        const chat = seedWorld(route.seedFor?.('owner'))
        assertOutcome(await call(route, chat, 'owner', { kind: 'chat' }), NOT_FOUND, `${route.id} chat session`)
        nothingHappened(chat)
      })
    }

    it('a malformed conversation id is refused by the validator, on the internal route as well', async () => {
      const w = seedWorld()
      for (const id of ['A7', 'A3']) {
        const route = ROUTES.find((r) => r.id === id)!
        const url = urlOf(route, { conversationId: 'not-an-id', messageId: w.messageIds.agent, agentKey: KEY })
        const out = await invoke(route, url, { user: { ...ACTORS.owner } })
        expect(out.error?.statusCode, id).to.equal(400)
      }
      nothingHappened(w)
    })

  })


  describe('internal stream route', () => {
    const route = ROUTES.find((r) => r.id === 'A3')!
    const scopedRequest = (email: string): Record<string, unknown> => ({ tokenPayload: { email, orgId: String(ORG) } })
    /** Hydration resolves the deployment's only org before it looks up the user. Stub after `seedWorld()`, which restores sinon. */
    const soleOrg = (): void => {
      sinon.stub(Org, 'find').returns({ limit: sinon.stub().returnsThis(), lean: sinon.stub().returnsThis(), exec: sinon.stub().resolves([{ _id: ORG }]) } as never)
    }
    const dbUser = (actor: ActorName) => ({
      _id: ACTORS[actor].userId,
      orgId: ORG,
      email: `${actor}@example.com`,
      fullName: actor,
      slug: actor,
    })

    it('a scoped token is hydrated to its user before the guard, and the owner streams', async () => {
      const w = seedWorld({ projectId: undefined, projectVisibility: undefined })
      soleOrg()
      sinon.stub(Users, 'findOne').resolves(dbUser('owner') as never)
      const p = invoke(route, urlOf(route, { conversationId: w.ids.agent, messageId: w.messageIds.agent }), scopedRequest('owner@example.com'))
      for (let i = 0; i < 20 && w.ai.streamCalls.length === 0; i += 1) await settle()
      w.ai.send('RUN_FINISHED', finalAnswer('ok'))
      w.ai.finish()
      const out = await p
      expect(out.error).to.equal(undefined)
      expect(out.status).to.equal(200)
      expect(w.ai.streamCalls).to.have.length(1)
    })

    it('a scoped token for another user in the org gets a JSON 404 and no turn', async () => {
      const w = seedWorld()
      soleOrg()
      sinon.stub(Users, 'findOne').resolves(dbUser('stranger') as never)
      const out = await invoke(route, urlOf(route, { conversationId: w.ids.agent, messageId: w.messageIds.agent }), scopedRequest('stranger@example.com'))
      assertOutcome(out, NOT_FOUND, 'A3 stranger')
      nothingHappened(w)
    })
  })

  describe('legacy parity decisions', () => {
    it('a project viewer can no longer cancel an agent run', async () => {
      const w = seedWorld()
      const route = ROUTES.find((r) => r.id === 'A5')!
      assertOutcome(await call(route, w, 'projectViewer'), NOT_FOUND, 'A5 project viewer')
      nothingHappened(w)
    })

    it('a recipient deleting an agent chat is a 404 (was an idempotent 200) and the chat survives', async () => {
      const w = seedWorld()
      const route = ROUTES.find((r) => r.id === 'A8')!
      assertOutcome(await call(route, w, 'write'), NOT_FOUND, 'A8 write recipient')
      expect(w.store.session(w.ids.agent)?.isDeleted).to.equal(false)
    })



    it('a bare isShared flag with no recipients grants a stranger nothing on regenerate', async () => {
      const w = seedWorld({ sharedWith: [], projectId: undefined, projectVisibility: undefined })
      const route = ROUTES.find((r) => r.id === 'A4')!
      assertOutcome(await call(route, w, 'stranger'), NOT_FOUND, 'A4 stranger')
      nothingHappened(w)
    })

    it('the owner still gets the same JSON from get-by-id as before', async () => {
      const w = seedWorld()
      const route = ROUTES.find((r) => r.id === 'A7')!
      const out = await call(route, w, 'owner')
      expect(out.error).to.equal(undefined)
      expect(out.status).to.equal(200)
      expect((out.res.jsonBody as { conversation: { title: string } }).conversation.title).to.equal('agent')
    })
  })
})
