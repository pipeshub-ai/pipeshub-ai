import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OPERATION_REQUIREMENTS } from '../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.policy'
import { LEGACY_REQUIREMENTS } from '../../../../src/modules/authz/domain/legacy'
import { rank } from '../../../../src/modules/authz/domain/ladder'
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { ConversationOperation } from '../../../../src/modules/enterprise_search/services/collaboration/domain/types'
import { CONVERSATION_ROUTES, ConversationRoute, routeKey } from '../helpers/conversation-routes'
import {
  ACTORS,
  ACTOR_NAMES,
  Actor,
  ActorName,
  Env,
  Expected,
  NOT_FOUND,
  Role,
  Seed,
  TEAM_WRITE,
  assertDecision,
  buildRouters,
  call,
  nothingHappened,
  seed,
} from '../helpers/conversation-world'

/**
 * N-ACL: every conversation route x every actor x both kinds x the flag, with the expected status and code derived from the
 * policy tables (`OPERATION_REQUIREMENTS`, `LEGACY_REQUIREMENTS`) rather than written out by hand. The guard is the real one;
 * a request it lets through is answered 299 before the handler, so this file is about the decision only.
 */

/** The flag-on decision for `role` doing `op`, read off the 51 §2 table. */
function expectedOn(op: ConversationOperation, role: Role, isAsker: boolean, ownerActive = true): Expected {
  if (role === 'none') return NOT_FOUND
  const req = OPERATION_REQUIREMENTS[op]
  const need = req.min === 'owner' ? 'owner' : req.min === 'write' ? 'editor' : 'viewer'
  const have = role === 'owner' ? 'owner' : role === 'write' ? 'editor' : 'viewer'
  if (rank(have) < rank(need)) return { status: 403, code: COLLAB_ERROR_CODES[req.belowMin] }
  if (req.guard === 'asker' && !isAsker) return { status: 403, code: COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED }
  if (req.requiresActiveOwner === true && !ownerActive && role !== 'owner') return { status: 403, code: COLLAB_ERROR_CODES.OWNER_INACTIVE }
  return 'allow'
}

/** The flag-off decision: the paths that exist for the actor against `LEGACY_REQUIREMENTS`; every denial is a 404. */
function expectedOff(route: ConversationRoute, a: Actor): Expected {
  const accepted = LEGACY_REQUIREMENTS[route.kind][route.op] as readonly string[]
  return a.paths.some((p) => p !== 'team' && accepted.includes(p)) ? 'allow' : NOT_FOUND
}

const route = (id: string): ConversationRoute => CONVERSATION_ROUTES.find((r) => r.id === id)!
const routesFor = (op: ConversationOperation): ConversationRoute[] => CONVERSATION_ROUTES.filter((r) => r.op === op)

describe('N-ACL: conversation routes x actors x flag, from the policy tables', () => {
  let on: Env
  let off: Env
  before(() => {
    on = buildRouters({ collab: true })
    off = buildRouters({ collab: false })
  })
  afterEach(() => {
    sinon.restore()
    on.teamCalls.count = off.teamCalls.count = 0
  })

  it('covers all 29 routes of PH-04 §3', () => {
    expect(CONVERSATION_ROUTES.map(routeKey)).to.have.length(29)
    expect(new Set(CONVERSATION_ROUTES.map((r) => `${r.kind} ${routeKey(r)}`)).size).to.equal(29)
  })

  describe('flag on: OPERATION_REQUIREMENTS', () => {
    for (const r of CONVERSATION_ROUTES) {
      for (const who of ACTOR_NAMES) {
        it(`${r.id} ${r.op} ${r.kind}: ${who}`, async () => {
          const s = seed()
          const expected = expectedOn(r.op, ACTORS[who].role, who === 'A')
          assertDecision(await call(on, r, s, who), expected, `${r.id} ${who}`)
          nothingHappened(s)
        })
      }
    }
  })

  describe('flag off: LEGACY_REQUIREMENTS, every denial a 404, no team lookups', () => {
    for (const r of CONVERSATION_ROUTES) {
      for (const who of ACTOR_NAMES) {
        it(`${r.id} ${r.op} ${r.kind}: ${who}`, async () => {
          const s = seed()
          assertDecision(await call(off, r, s, who), expectedOff(r, ACTORS[who]), `${r.id} ${who}`)
          expect(off.teamCalls.count, 'team directory calls').to.equal(0)
          nothingHappened(s)
        })
      }
    }
  })

  describe('named cells from 60 §10', () => {
    it('flag on: write through a team sends, read through a team does not, a project viewer does not (PI-02), a project editor does (PI-03)', async () => {
      for (const id of ['C1', 'A1']) {
        const send = route(id)
        assertDecision(await call(on, send, seed(), 'Bt'), 'allow', `${id} Bt`)
        assertDecision(await call(on, send, seed(), 'Ct'), { status: 403, code: COLLAB_ERROR_CODES.READ_ONLY }, `${id} Ct`)
        assertDecision(await call(on, send, seed(), 'P'), { status: 403, code: COLLAB_ERROR_CODES.READ_ONLY }, `${id} P`)
        assertDecision(await call(on, send, seed(), 'PE'), 'allow', `${id} PE`)
        assertDecision(await call(on, send, seed(), 'D'), NOT_FOUND, `${id} D`)
        assertDecision(await call(on, send, seed(), 'E'), NOT_FOUND, `${id} E`)
      }
    })

    it('TM-01: a direct write row decides send without a team lookup, although a team row exists', async () => {
      assertDecision(await call(on, route('C1'), seed(), 'B'), 'allow', 'C1 B')
      expect(on.teamCalls.count).to.equal(0)
    })

    it('SEC-02: a read recipient is not lifted by another recipient’s write row, on any write-gated route', async () => {
      for (const r of CONVERSATION_ROUTES.filter((x) => OPERATION_REQUIREMENTS[x.op].min !== 'read')) {
        const code = OPERATION_REQUIREMENTS[r.op].belowMin === 'OWNER_ONLY' ? COLLAB_ERROR_CODES.OWNER_ONLY : COLLAB_ERROR_CODES.READ_ONLY
        assertDecision(await call(on, r, seed(), 'C'), { status: 403, code }, `${r.id} C`)
      }
    })

    it('SEC-03: a stranger gets 404 on regenerate of a chat flagged isShared with no recipients, flag on and off', async () => {
      for (const env of [on, off]) {
        for (const id of ['C11', 'A4']) {
          const s = seed({ isShared: true, sharedWith: [], projectId: undefined, projectVisibility: undefined })
          assertDecision(await call(env, route(id), s, 'D'), NOT_FOUND, `${id} D`)
          nothingHappened(s)
        }
      }
    })

    it('SEC-04: a read recipient deleting is 403 with the flag on and 404 with it off, and the chat survives', async () => {
      for (const id of ['C6', 'A8']) {
        const r = route(id)
        const withFlag = seed()
        assertDecision(await call(on, r, withFlag, 'C'), { status: 403, code: COLLAB_ERROR_CODES.OWNER_ONLY }, `${id} on`)
        expect(withFlag.store.session(withFlag.ids[r.kind])?.isDeleted).to.equal(false)
        const noFlag = seed()
        assertDecision(await call(off, r, noFlag, 'C'), NOT_FOUND, `${id} off`)
        expect(noFlag.store.session(noFlag.ids[r.kind])?.isDeleted).to.equal(false)
      }
    })

    it('a deleted conversation, an agent id on a chat route and a chat id on an agent route are 404 for the owner, flag on and off', async () => {
      for (const env of [on, off]) {
        for (const r of CONVERSATION_ROUTES) {
          const deleted = seed({ isDeleted: true })
          assertDecision(await call(env, r, deleted, 'A'), NOT_FOUND, `${r.id} deleted`)
          const other = seed()
          assertDecision(await call(env, r, other, 'A', r.kind === 'chat' ? 'agent' : 'chat'), NOT_FOUND, `${r.id} wrong kind`)
        }
      }
    })
  })

  describe('regenerate: only the asker, owner included (O-1)', () => {
    it('legacy questions without an author belong to the owner, so an editor gets REGENERATE_NOT_ALLOWED (PH04-06)', async () => {
      for (const id of ['C11', 'A4']) {
        assertDecision(await call(on, route(id), seed(), 'A'), 'allow', `${id} A`)
        assertDecision(await call(on, route(id), seed(), 'B'), { status: 403, code: COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED }, `${id} B`)
      }
    })

    it('a question authored by the editor can be regenerated by the editor and no longer by the owner', async () => {
      for (const id of ['C11', 'A4']) {
        assertDecision(await call(on, route(id), seed({}, ACTORS.B.userId), 'B'), 'allow', `${id} B`)
        assertDecision(await call(on, route(id), seed({}, ACTORS.B.userId), 'A'), { status: 403, code: COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED }, `${id} A`)
        assertDecision(await call(on, route(id), seed({}, ACTORS.B.userId), 'C'), { status: 403, code: COLLAB_ERROR_CODES.READ_ONLY }, `${id} C`)
      }
    })
  })

  describe('D11: ops that require an active owner', () => {
    it('an editor is refused with OWNER_INACTIVE on exactly the ops the table flags; reads and the rest are unaffected', async () => {
      const inactive = buildRouters({ collab: true, ownerActive: false })
      for (const r of CONVERSATION_ROUTES) {
        // The asker of the answered question is B, so the regenerate author check passes and the owner check is what decides.
        const s = seed({}, ACTORS.B.userId)
        for (const who of ['B', 'Bt', 'PE'] as ActorName[]) {
          assertDecision(await call(inactive, r, s, who), expectedOn(r.op, ACTORS[who].role, who === 'B', false), `${r.id} ${who}`)
        }
        assertDecision(await call(inactive, r, s, 'A'), expectedOn(r.op, 'owner', r.op === 'regenerate' ? false : true, false) === 'allow' ? 'allow' : expectedOn(r.op, 'owner', false, false), `${r.id} A`)
      }
      expect(CONVERSATION_ROUTES.some((r) => OPERATION_REQUIREMENTS[r.op].requiresActiveOwner === true)).to.equal(true)
    })

    it('a flagged op is refused for the editor and a read is not', async () => {
      const inactive = buildRouters({ collab: true, ownerActive: false })
      assertDecision(await call(inactive, route('C1'), seed(), 'B'), { status: 403, code: COLLAB_ERROR_CODES.OWNER_INACTIVE }, 'C1 B')
      assertDecision(await call(inactive, route('C5'), seed(), 'B'), 'allow', 'C5 B')
    })
  })

  describe('SEC-14: the detail read through a team share', () => {
    it('opens for a read-via-team caller and shows no other recipients', async () => {
      const real = buildRouters({ collab: true, stop: false })
      const s = seed()
      const out = await call(real, route('C5'), s, 'Ct')
      expect(out.error).to.equal(undefined)
      expect(out.status).to.equal(200)
      const conversation = (out.body as { conversation: Record<string, unknown> }).conversation
      expect(conversation.title).to.equal('chat')
      expect(JSON.stringify(out.body)).to.not.contain(TEAM_WRITE)
      expect(JSON.stringify(out.body)).to.not.contain(String(ACTORS.B.userId))
      expect(JSON.parse(JSON.stringify(conversation))).to.not.have.property('sharedWith')
    })
  })
})
