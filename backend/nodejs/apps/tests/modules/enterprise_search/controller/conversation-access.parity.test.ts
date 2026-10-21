import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CONVERSATION_ROUTES, ConversationRoute } from '../helpers/conversation-routes'
import { ACTOR_NAMES, ActorName, Env, NOT_FOUND, assertDecision, buildRouters, call, nothingHappened, seed } from '../helpers/conversation-world'

/**
 * by default the guards decide as the handlers did before the guards existed. The expectations are
 * written out per route from that old behaviour, not derived from `LEGACY_REQUIREMENTS`, so a change to the table shows up here.
 *
 * Actors: A owner, B is the write recipient F (a legacy `write` row), C the read recipient R, P a project viewer of a
 * project-visible chat, Bt/Ct share only through a team row, D a same-org stranger, E another org.
 */

/** Who besides the owner got past the old handler's filter, by route. Everything not listed is the owner alone. */
const BESIDES_OWNER: Record<string, ActorName[]> = {
  // GET /:id: owner, recipients (a write row is read), project members of a project-visible chat.
  C5: ['B', 'C', 'P', 'PE'],
  // Feedback: owner and recipients, not project members.
  C14: ['B', 'C'],
  // Agent open: owner, recipients (read-only, a write row is read) and project members.
  A7: ['B', 'C', 'P', 'PE'],
}

describe('default parity: every conversation route decides as it did before the guards', () => {
  let off: Env
  before(() => {
    off = buildRouters({})
  })
  afterEach(() => {
    sinon.restore()
  })

  for (const r of CONVERSATION_ROUTES) {
    it(`${r.method.toUpperCase()} ${r.path}: ${(BESIDES_OWNER[r.id] ?? []).join(',') || 'only the owner'} pass, every denial is a 404`, async () => {
      const allowed = new Set<ActorName>(['A', ...(BESIDES_OWNER[r.id] ?? [])])
      for (const who of ACTOR_NAMES) {
        const s = seed()
        const out = await call(off, r, s, who)
        assertDecision(out, allowed.has(who) ? 'allow' : NOT_FOUND, `${r.id} ${who}`)
        if (!allowed.has(who)) nothingHappened(s)
      }
    })
  }

  it('a write recipient (F) is treated exactly as a read recipient (R) on every route', async () => {
    for (const r of CONVERSATION_ROUTES) {
      const f = await call(off, r, seed(), 'B')
      const reader = await call(off, r, seed(), 'C')
      expect({ status: f.status, code: f.error?.code }, r.id).to.deep.equal({ status: reader.status, code: reader.error?.code })
    }
  })

  it('a recipient may read a chat and rate its answers, and may read the same agent chat but not rate it', async () => {
    const byId = (id: string): ConversationRoute => CONVERSATION_ROUTES.find((x) => x.id === id)!
    for (const [id, expected] of [['C5', 'allow'], ['C14', 'allow'], ['A7', 'allow'], ['A6', NOT_FOUND]] as const) {
      assertDecision(await call(off, byId(id), seed(), 'C'), expected, `${id} R`)
    }
  })

  it('team rows are ignored: a caller who is only in the sharing team gets a 404', async () => {
    for (const r of CONVERSATION_ROUTES) {
      for (const who of ['Bt', 'Ct'] as const) assertDecision(await call(off, r, seed(), who), NOT_FOUND, `${r.id} ${who}`)
    }
  })

  describe('recorded deviations', () => {
    const route = (id: string): ConversationRoute => CONVERSATION_ROUTES.find((x) => x.id === id)!

    it('a recipient who streams gets a JSON 404 with no event-stream header (was 200 + RUN_ERROR)', async () => {
      for (const id of ['C3', 'C4', 'A2', 'A3']) {
        const s = seed()
        const out = await call(off, route(id), s, 'C')
        assertDecision(out, NOT_FOUND, id)
        expect(out.res.headersSent, `${id} headers`).to.equal(false)
        expect(out.res.headers['content-type'], `${id} content type`).to.equal(undefined)
        expect(out.res.body, `${id} SSE body`).to.equal('')
        expect(out.res.eventsOf('RUN_ERROR'), `${id} RUN_ERROR`).to.have.length(0)
      }
    })

    it('a project viewer can no longer cancel an agent run (it was open to them)', async () => {
      assertDecision(await call(off, route('A5'), seed(), 'P'), NOT_FOUND, 'A5 P')
    })

    it('a recipient deleting an agent chat is a 404 and the chat survives (was an idempotent 200)', async () => {
      const s = seed()
      assertDecision(await call(off, route('A8'), s, 'C'), NOT_FOUND, 'A8 R')
      expect(s.store.session(s.ids.agent)?.isDeleted).to.equal(false)
    })
  })
})
